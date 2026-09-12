package com.comfyuistudio.terminal.fcm

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import com.comfyuistudio.terminal.R
import com.comfyuistudio.terminal.gallery.GeneratedImageSaver
import com.comfyuistudio.terminal.pairing.RemotePairingClient
import com.comfyuistudio.terminal.pairing.TokenStore
import com.comfyuistudio.terminal.terminal.TerminalActivity
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlin.concurrent.thread

/**
 * §Этап 6.5 дорожной карты ("Push-уведомления") -- принимающая сторона
 * на телефоне. Единственный источник push -- `fcm.py` на сервере (см.
 * его докстринг): `generation.completed`/`generation.error`, то же
 * самое событие, что и WS-канал (§2), просто доставленное и тогда,
 * когда WebView не выполняется (свёрнутое приложение/выключенный
 * экран) -- push НЕ заменяет WS, а дополняет его для этого одного
 * случая (см. §Этап 6.5: "единственная функция, не укладывающаяся в
 * модель чистого терминала").
 *
 * Обе обязанности этого класса -- ровно то, что перечислено в §Этап
 * 6.5, п.3 "Реализация" ("получение токена при первом запуске,
 * обновление на сервере при его смене... обработка входящего push...
 * по тапу открывает TerminalActivity сразу на нужном апп"). Получение
 * НАЧАЛЬНОГО токена (при первом запуске, для передачи в pair/confirm)
 * -- не здесь, а в PairingScreen.kt, т.к. до pairing ещё нет
 * TokenStore.Device, на который можно было бы опереться в onNewToken.
 */
class FcmService : FirebaseMessagingService() {

    companion object {
        private const val CHANNEL_ID = "generation_events"
        private const val NOTIFICATION_ID = 1
        // Совпадает с путём, который routes/home.py даёт плитке Imagine
        // (RemoteApp.path в apps_registry.py) -- единственное
        // зарегистрированное на сегодня приложение (см. §Этап 6.4), по
        // этому же полю в будущем можно будет различать, из-под какого
        // именно апп пришло уведомление, если их станет больше.
        private const val IMAGINE_PATH = "/apps/imagine/"
    }

    /**
     * Firebase переиздаёт токен НЕ при каждом запуске -- когда это
     * случается уже ПОСЛЕ pairing, сервер должен узнать новый токен,
     * иначе push продолжат уходить на невалидный (см. §Этап 6.5, п.3:
     * "FCM токены иногда переиздаются"). Если устройство ещё не
     * сопряжено (TokenStore пуст) -- сохранять некуда, тихо выходим:
     * PairingScreen.kt сам заберёт актуальный токен при следующем
     * pairing.
     */
    override fun onNewToken(token: String) {
        super.onNewToken(token)
        val device = TokenStore(applicationContext).load() ?: return
        // Сетевой вызов -- не в главном потоке; FirebaseMessagingService
        // сам уже вызывает onNewToken не в UI-потоке, но на всякий
        // случай (документация Firebase этого явно не гарантирует для
        // всех версий SDK) -- отдельный поток, без каких-либо Compose/
        // UI зависимостей, простой thread { } достаточен для одного
        // короткого HTTP-вызова.
        thread {
            RemotePairingClient().updateFcmToken(device.host, device.port, device.accessToken, token)
        }
    }

    /**
     * Firebase сам показывает уведомление автоматически, только если в
     * сообщении есть блок `notification` И приложение свёрнуто -- на
     * переднем плане `onMessageReceived` вызывается всегда, поэтому
     * строим уведомление руками сами, единообразно в обоих случаях
     * (см. `message.notification` в fcm.py -- сервер всегда шлёт этот
     * блок, так что здесь он тоже всегда есть).
     */
    override fun onMessageReceived(message: RemoteMessage) {
        super.onMessageReceived(message)
        val title = message.notification?.title ?: message.data["title"] ?: return
        val body = message.notification?.body ?: message.data["body"] ?: ""

        ensureChannel()

        val tapIntent = Intent(this, TerminalActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            // См. докстринг класса про IMAGINE_PATH -- TerminalActivity
            // при наличии этого extra сразу открывает соответствующий
            // апп, а не домашнюю страницу со списком плиток.
            //
            // НОВОЕ (живой отчёт после первого прогона на реальном
            // устройстве, §Этап 6.5): просто открыть IMAGINE_PATH было
            // недостаточно -- галерея Imagine целиком в памяти JS
            // текущей загрузки страницы (см. static/js/app.js), поэтому
            // тап открывал ПУСТУЮ страницу, а не результат ИМЕННО той
            // генерации, о которой пришло уведомление. `prompt_id` уже
            // есть в data-payload push-сообщения (см. fcm.py на
            // сервере) -- добавляем его в query, а фронтенд Imagine
            // (`initDeepLinkedGeneration()` в app.js) сам подхватывает
            // его при загрузке и подгружает готовый результат из
            // истории ComfyUI, независимо от того, какая сессия
            // изначально запускала генерацию.
            val promptId = message.data["prompt_id"]
            val path = if (!promptId.isNullOrBlank()) {
                "$IMAGINE_PATH?prompt_id=${Uri.encode(promptId)}"
            } else {
                IMAGINE_PATH
            }
            putExtra(TerminalActivity.EXTRA_OPEN_PATH, path)
        }
        val pendingIntent = PendingIntent.getActivity(
            this, 0, tapIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(body)
            .setAutoCancel(true)
            .setContentIntent(pendingIntent)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()

        val manager = getSystemService(NotificationManager::class.java)
        // POST_NOTIFICATIONS (API 33+) запрашивается в MainActivity при
        // первом запуске -- если пользователь его не дал, notify()
        // здесь просто молча ничего не покажет (стандартное поведение
        // системы), а не упадёт с SecurityException.
        manager?.notify(NOTIFICATION_ID, notification)

        maybeSaveGeneratedImages(message)
    }

    /**
     * НОВОЕ (по запросу пользователя после живого теста §Этапа 7):
     * автосохранение сгенерированных картинок в галерею телефона --
     * см. докстринг GeneratedImageSaver.kt про то, почему именно push
     * (а не что-то внутри WebView) -- это единственное событие,
     * долетающее до телефона независимо от того, открыт ли сейчас
     * Imagine.
     *
     * "state" -- см. НОВОЕ поле в fcm.py на сервере (раньше здесь
     * пришлось бы сравнивать локализованный текст заголовка "Готово"/
     * "Ошибка генерации", что хрупко) -- сохраняем только для
     * успешного завершения, не для ошибок (там и сохранять нечего).
     */
    private fun maybeSaveGeneratedImages(message: RemoteMessage) {
        if (message.data["state"] != "generation.completed") return
        val promptId = message.data["prompt_id"]?.takeIf { it.isNotBlank() } ?: return
        val device = TokenStore(applicationContext).load() ?: return
        // Сетевые вызовы -- не в главном потоке (тот же приём, что и у
        // onNewToken выше): пара блокирующих HTTP-запросов
        // (статус + скачивание каждой картинки, см.
        // GeneratedImageSaver.saveGenerationImages) и запись в
        // MediaStore не должны выполняться в потоке, в котором система
        // и так уже вызывает onMessageReceived.
        thread {
            GeneratedImageSaver.saveGenerationImages(applicationContext, device, promptId)
        }
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Генерация изображений",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = "Уведомления о завершении генерации в Imagine"
        }
        manager.createNotificationChannel(channel)
    }
}
