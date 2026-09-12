package com.comfyuistudio.terminal.gallery

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.util.Log
import com.comfyuistudio.terminal.pairing.TokenStore
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * `gallery/GeneratedImageSaver.kt` -- НОВАЯ функция (по запросу
 * пользователя после живого теста §Этапа 7): "сохранять все
 * сгенерированные изображения (те, что запускались с телефона) на
 * телефон, только картинку, без дополнительных файлов".
 *
 * Единственный ТЕКУЩИЙ канал, который у Remote вообще есть для "это
 * генерация именно ДЛЯ этого телефона" -- push (§Этап 6.5): сервер шлёт
 * generation.completed ВСЕМ сопряжённым устройствам на КАЖДУЮ
 * завершившуюся генерацию (см. generation_watcher.py/fcm.py на
 * сервере -- различения "кто именно поставил эту генерацию в очередь"
 * там пока не существует), и это же событие уже долетает до телефона,
 * даже когда WebView не открыт (свёрнутое приложение/выключенный
 * экран, ровно то, ради чего push вообще заводился). Поэтому
 * автосохранение цепляется именно к этому событию в FcmService.kt, а
 * не к чему-то внутри WebView/app.js -- это и надёжнее (работает
 * независимо от того, открыт ли сейчас Imagine), и не требует
 * протаскивать новый JS-мост туда, где раньше был осознанно только
 * WS/cookie-токен (см. §0.3, "терминал не хранит собственную бизнес-
 * логику").
 *
 * Скачивание и сохранение -- ДВА отдельных HTTP-запроса через тот же
 * reverse-proxy Remote (`imagine_proxy.py`), тем же Bearer-токеном
 * устройства, что и у остального нативного кода (RemotePairingClient.kt):
 *   1. GET .../apps/imagine/api/generate/{promptId}/status -- тот же
 *      эндпоинт, что и у deep-link'а из TerminalActivity (см.
 *      FcmService.kt про prompt_id в payload) -- сам push НЕ несёт
 *      URL картинок (см. fcm.py: только prompt_id), поэтому список
 *      получаем отдельно.
 *   2. GET .../apps/imagine/{img.url} на каждую картинку -- `img.url`
 *      уже относительный (см. докстринг у "url" в
 *      imagine/backend/main.py::generation_status), просто дописываем
 *      префикс прокси.
 *
 * "Только картинку, без дополнительных файлов" -- в MediaStore
 * записываются РОВНО байты ответа сервера (то, что ComfyUI уже
 * сохранил как PNG/JPEG на диске) под тем же именем файла, никакого
 * отдельного .json/.txt с воркфлоу/метаданными рядом не создаётся (в
 * отличие от, например, автосохранения воркфлоу самим ComfyUI на ПК).
 */
object GeneratedImageSaver {
    private const val TAG = "GeneratedImageSaver"

    // См. докстринг про повторные попытки в saveGenerationImages.
    private const val MAX_ATTEMPTS = 3
    private const val RETRY_DELAY_MS = 1500L

    // Отдельный подальбом, а не прямо в корень "Изображения" -- чтобы
    // сгенерированное явно отличалось от обычных фото телефона в
    // системной галерее.
    private const val ALBUM_NAME = "ComfyUI Studio"

    private val client = OkHttpClient.Builder()
        // НОВОЕ (живой отчёт: "не удалось сохранить ...: timeout"):
        // прежние 5с/20с были рассчитаны на маленькие JSON-ответы
        // (см. RemotePairingClient.kt, где взяты эти же цифры) -- для
        // самой картинки это может быть слишком мало. Цепочка запроса
        // -- телефон → Remote (imagine_proxy.py, стриминг) → Imagine
        // (`GET /api/image`, синхронный обработчик в threadpool) →
        // ComfyUI (`fetch_image_bytes`, свой таймаут 10с) -- три хопа,
        // и если ComfyUI в этот момент занят следующей генерацией
        // (что вполне вероятно сразу после push о завершении ПРЕДЫДУЩЕЙ
        // -- следующая уже может стоять в очереди и стартовать), общее
        // время может ощутимо вырасти. Здесь не устраняется сама
        // причина потенциальной задержки (это может быть просто нагрузка
        // на ComfyUI, а не баг) -- только даём больше времени, прежде чем
        // сдаваться.
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    /**
     * Синхронная и блокирующая (обычный OkHttp `execute()`, не
     * `enqueue()`) -- вызывающая сторона (FcmService.onMessageReceived)
     * сама уводит вызов на фоновый поток (тот же приём, что уже
     * применяется там для `updateFcmToken`, см. её докстринг про
     * `thread { }`), поэтому здесь блокирующий ввод-вывод ожидаем и не
     * требует собственной корутины/экзекьютора.
     */
    fun saveGenerationImages(context: Context, device: TokenStore.Device, promptId: String) {
        val relativeUrls = fetchImageUrls(device, promptId)
        if (relativeUrls.isEmpty()) {
            // Либо генерация без картинок (нечего сохранять), либо
            // Imagine/Remote не ответили -- в обоих случаях тихо
            // выходим: это фоновая функция без UI, показывать тут
            // отдельную ошибку пользователю не на чем (сама push-
            // нотификация про успех/ошибку генерации уже показана
            // FcmService.onMessageReceived независимо от этой функции).
            return
        }
        for (relativeUrl in relativeUrls) {
            // НОВОЕ (живой отчёт: из двух генераций по 2 картинки одна
            // картинка так и не сохранилась -- в обоих случаях именно
            // ВТОРАЯ по счёту, что указывает на протухшее keep-alive
            // соединение, см. её же исправление в remote/__main__.py
            // (timeout_keep_alive=75)). Тот фикс должен устранить саму
            // причину, но пара быстрых повторов здесь -- дешёвая защита
            // на случай ЛЮБОЙ другой преходящей сетевой заминки, раз
            // цена такой ошибки -- незаметно потерянная картинка, а не
            // просто медленный ответ.
            var lastError: IOException? = null
            var saved = false
            for (attempt in 1..MAX_ATTEMPTS) {
                try {
                    downloadAndSave(context, device, relativeUrl)
                    saved = true
                    break
                } catch (e: IOException) {
                    lastError = e
                    if (attempt < MAX_ATTEMPTS) Thread.sleep(RETRY_DELAY_MS)
                }
            }
            if (!saved) {
                Log.w(TAG, "Не удалось сохранить $relativeUrl после $MAX_ATTEMPTS попыток: ${lastError?.message}")
            }
        }
    }

    private fun fetchImageUrls(device: TokenStore.Device, promptId: String): List<String> {
        val statusUrl = "http://${device.host}:${device.port}/apps/imagine/api/generate/$promptId/status"
        val request = Request.Builder()
            .url(statusUrl)
            .header("Authorization", "Bearer ${device.accessToken}")
            .build()
        return try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return emptyList()
                val json = JSONObject(response.body?.string().orEmpty())
                if (json.optString("state") != "done") return emptyList()
                val images = json.optJSONArray("images") ?: return emptyList()
                (0 until images.length()).mapNotNull { i ->
                    images.optJSONObject(i)?.optString("url")?.takeIf { it.isNotBlank() }
                }
            }
        } catch (e: IOException) {
            Log.w(TAG, "Не удалось получить статус генерации $promptId: ${e.message}")
            emptyList()
        }
    }

    private fun downloadAndSave(context: Context, device: TokenStore.Device, relativeUrl: String) {
        val fullUrl = "http://${device.host}:${device.port}/apps/imagine/$relativeUrl"
        val request = Request.Builder()
            .url(fullUrl)
            .header("Authorization", "Bearer ${device.accessToken}")
            .build()

        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                Log.w(TAG, "Сервер отклонил скачивание $relativeUrl: ${response.code}")
                return
            }
            val bytes = response.body?.bytes() ?: return
            val mimeType = response.header("Content-Type")?.substringBefore(";")?.trim()
                ?: "image/png"
            writeToGallery(context, fileNameFrom(relativeUrl, mimeType), mimeType, bytes)
        }
    }

    /** Имя файла ComfyUI (после "filename=" в query, см. докстринг
     * класса про "url") уже уникально само по себе (ComfyUI сам
     * нумерует последовательно) -- переиспользуем как есть вместо
     * собственной схемы именования. */
    private fun fileNameFrom(relativeUrl: String, mimeType: String): String {
        val fromQuery = relativeUrl.substringAfter("filename=", "").substringBefore("&")
        if (fromQuery.isNotBlank()) return fromQuery
        val ext = if (mimeType.contains("png")) "png" else "jpg"
        return "comfyui_${System.currentTimeMillis()}.$ext"
    }

    private fun writeToGallery(context: Context, fileName: String, mimeType: String, bytes: ByteArray) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            writeViaMediaStoreScoped(context, fileName, mimeType, bytes)
        } else {
            writeViaLegacyPublicDirectory(context, fileName, mimeType, bytes)
        }
    }

    /** API 29+ (Scoped Storage) -- никакого разрешения WRITE_EXTERNAL_STORAGE
     * не требуется: ContentResolver сам создаёт файл в разделе
     * "Изображения" приложения, сразу видимый системной галерее. */
    private fun writeViaMediaStoreScoped(
        context: Context,
        fileName: String,
        mimeType: String,
        bytes: ByteArray,
    ) {
        val resolver = context.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, fileName)
            put(MediaStore.Images.Media.MIME_TYPE, mimeType)
            put(MediaStore.Images.Media.RELATIVE_PATH, "${Environment.DIRECTORY_PICTURES}/$ALBUM_NAME")
            // IS_PENDING -- файл не показывается другим приложениям
            // (в т.ч. самой галерее), пока запись не закончена -- иначе
            // при большой картинке галерея могла бы показать/открыть
            // ещё недописанный файл.
            put(MediaStore.Images.Media.IS_PENDING, 1)
        }
        val itemUri = resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
        if (itemUri == null) {
            Log.w(TAG, "MediaStore отклонил вставку для $fileName")
            return
        }
        resolver.openOutputStream(itemUri)?.use { out -> out.write(bytes) }
        values.clear()
        values.put(MediaStore.Images.Media.IS_PENDING, 0)
        resolver.update(itemUri, values, null, null)
    }

    /** API 26-28 -- до Scoped Storage; пишем напрямую в публичную папку
     * "Изображения/ComfyUI Studio" (нужно разрешение
     * WRITE_EXTERNAL_STORAGE, см. AndroidManifest.xml/MainActivity.kt).
     * Без разрешения запись здесь просто упадёт с IOException --
     * ловится вызывающей стороной (saveGenerationImages), не роняя
     * весь процесс сохранения остальных картинок. */
    private fun writeViaLegacyPublicDirectory(context: Context, fileName: String, mimeType: String, bytes: ByteArray) {
        val picturesDir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_PICTURES)
        val albumDir = File(picturesDir, ALBUM_NAME)
        if (!albumDir.exists() && !albumDir.mkdirs()) {
            Log.w(TAG, "Не удалось создать папку $albumDir")
            return
        }
        val target = File(albumDir, fileName)
        FileOutputStream(target).use { it.write(bytes) }
        // На API < 29 системная галерея не подхватывает новые файлы
        // сама -- без сканирования файл был бы виден только через
        // файловый менеджер, но не в приложении "Фото". Первый аргумент
        // -- именно Context (см. живой баг сборки: раньше здесь по
        // ошибке передавался File, а не Context, компилятор Kotlin
        // сразу же на это указал).
        android.media.MediaScannerConnection.scanFile(
            context,
            arrayOf(target.absolutePath),
            arrayOf(mimeType),
            null,
        )
    }
}
