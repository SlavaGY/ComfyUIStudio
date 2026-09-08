package com.comfyuistudio.terminal.pairing

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Единственный REST-вызов, который делает нативный код терминала —
 * `POST /api/v1/remote/pair/confirm` (см. routes/pairing_routes.py на
 * сервере). Не файл "RemoteApi.kt со всеми моделями" — роадмап (§Этап
 * 6, "Что на телефоне") прямо говорит "никакого RemoteApi.kt/
 * native-моделей QueueState/GenerationState" — вся эта логика теперь
 * в JS-фронтенде Imagine внутри WebView. Pairing — единственное
 * исключение, потому что до появления токена показывать WebView
 * попросту нечем (нет сессии, см. §0.3, пункт 2).
 *
 * Файла с таким именем нет в дереве §Этап 6 дорожной карты — это
 * минимальное дополнение к PairingScreen.kt/TokenStore.kt, отделяющее
 * сетевой вызов от Compose-кода экрана; сама дорожная карта не
 * запрещает вспомогательные файлы такого рода (в отличие от явно
 * запрещённых RemoteApi.kt/RemoteWebSocket.kt).
 *
 * `updateFcmToken` добавлен на §Этапе 6.5 ("Push-уведомления") — тот же
 * клиент, тот же принцип: единственный дополнительный REST-вызов,
 * который телефон делает сам (`POST /fcm-token`, см. routes/fcm.py на
 * сервере) — обновление токена, когда Firebase переиздаёт его уже
 * ПОСЛЕ pairing (см. FcmService.kt::onNewToken).
 */
class RemotePairingClient {

    sealed class Result {
        data class Success(val deviceId: String, val accessToken: String) : Result()
        data class Failure(val message: String) : Result()
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    /**
     * @param host/port — адрес Remote (из discovery либо введённый вручную).
     * @param code — 6-значный код формата "482-731" (см. models.py::PairingCodeResponse
     *   на сервере), как его ввёл пользователь.
     * @param deviceName — по умолчанию имя модели телефона, редактируемо
     *   пользователем на экране Pairing.
     * @param fcmToken — см. §Этап 6.5: опционален, null, если Firebase
     *   ещё не выдал токен к моменту pairing (или push вообще недоступен
     *   на этом сборке/устройстве, см. PairingScreen.kt) — сервер и так
     *   трактует отсутствие токена как "push для этого устройства не
     *   отправляются", это не ошибка.
     */
    fun confirm(host: String, port: Int, code: String, deviceName: String, fcmToken: String? = null): Result {
        val body = JSONObject()
            .put("code", code)
            .put("device_name", deviceName)
            .apply { if (fcmToken != null) put("fcm_token", fcmToken) }
            .toString()
            .toRequestBody("application/json".toMediaType())

        val request = Request.Builder()
            .url("http://$host:$port/api/v1/remote/pair/confirm")
            .post(body)
            .build()

        return try {
            client.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    // Сервер отдаёт {"detail": "..."} при 400 (см.
                    // PairingError -> HTTPException в pairing_routes.py).
                    val detail = runCatching { JSONObject(text).optString("detail") }.getOrNull()
                    return Result.Failure(detail?.takeIf { it.isNotBlank() } ?: "Ошибка сервера (${response.code})")
                }
                val json = JSONObject(text)
                Result.Success(
                    deviceId = json.getString("device_id"),
                    accessToken = json.getString("access_token"),
                )
            }
        } catch (e: IOException) {
            Result.Failure("Не удалось подключиться к ${host}:${port}: ${e.message}")
        }
    }

    /**
     * `POST /fcm-token` — вызывается из FcmService.kt::onNewToken, когда
     * Firebase переиздаёт токен УЖЕ сопряжённому устройству (см.
     * докстринг класса). Не бросает исключений — ошибка здесь означает
     * "сервер сейчас недоступен/выключен", а не что-то, что стоит
     * показывать пользователю; следующий onNewToken (или следующий
     * успешный вызов из другого места) просто попробует снова.
     */
    fun updateFcmToken(host: String, port: Int, accessToken: String, fcmToken: String): Boolean {
        val body = JSONObject()
            .put("fcm_token", fcmToken)
            .toString()
            .toRequestBody("application/json".toMediaType())

        val request = Request.Builder()
            .url("http://$host:$port/api/v1/remote/fcm-token")
            .header("Authorization", "Bearer $accessToken")
            .post(body)
            .build()

        return try {
            client.newCall(request).execute().use { it.isSuccessful }
        } catch (e: IOException) {
            false
        }
    }
}
