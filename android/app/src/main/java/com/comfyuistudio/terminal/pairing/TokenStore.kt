package com.comfyuistudio.terminal.pairing

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Хранилище сопряжённого устройства — §Этап 6 дорожной карты
 * (ComfyUIStudio_Remote_Roadmap.md): `EncryptedSharedPreferences`, как
 * и было решено в разделе "Что на телефоне" (§0.3) — `access_token`
 * достаточно чувствителен (даёт полный доступ к Remote API этого ПК,
 * см. auth.py на сервере), чтобы не держать его в обычных
 * SharedPreferences открытым текстом.
 *
 * Хранит РОВНО одно устройство за раз — терминал этого приложения
 * подключается к одному ПК (§0: "Android никогда не обращается к
 * ComfyUI/Imagine напрямую... только к Remote API" одного конкретного
 * сервера). Если пользователю понадобится несколько ПК одновременно
 * (упомянуто как побочная возможность в §0.3, "кнопка смены ПК, если
 * сопряжено несколько") — это расширение схемы на будущий этап, не
 * блокирует MVP этапа 6.
 */
class TokenStore(context: Context) {

    private val prefs: SharedPreferences by lazy {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            PREFS_FILE_NAME,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    data class Device(
        val deviceId: String,
        val accessToken: String,
        val host: String,
        val port: Int,
        val deviceName: String,
    )

    fun save(device: Device) {
        prefs.edit()
            .putString(KEY_DEVICE_ID, device.deviceId)
            .putString(KEY_ACCESS_TOKEN, device.accessToken)
            .putString(KEY_HOST, device.host)
            .putInt(KEY_PORT, device.port)
            .putString(KEY_DEVICE_NAME, device.deviceName)
            .apply()
    }

    fun load(): Device? {
        val deviceId = prefs.getString(KEY_DEVICE_ID, null) ?: return null
        val accessToken = prefs.getString(KEY_ACCESS_TOKEN, null) ?: return null
        val host = prefs.getString(KEY_HOST, null) ?: return null
        val port = prefs.getInt(KEY_PORT, -1)
        if (port <= 0) return null
        val deviceName = prefs.getString(KEY_DEVICE_NAME, "") ?: ""
        return Device(deviceId, accessToken, host, port, deviceName)
    }

    /** Используется MainActivity для роутинга Pairing vs Terminal. */
    fun hasDevice(): Boolean = load() != null

    /** "Сменить ПК" / отвязать устройство локально (сам сервер узнает
     * об этом только при следующей попытке достучаться до отозванного
     * токена — см. devices.py::revoke на сервере; локальное удаление
     * здесь не обязано быть онлайн-операцией). */
    fun clear() {
        prefs.edit().clear().apply()
    }

    private companion object {
        const val PREFS_FILE_NAME = "remote_device_secure"
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_ACCESS_TOKEN = "access_token"
        const val KEY_HOST = "host"
        const val KEY_PORT = "port"
        const val KEY_DEVICE_NAME = "device_name"
    }
}
