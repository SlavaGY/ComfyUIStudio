package com.comfyuistudio.terminal.tunnel

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Хранилище WireGuard-конфига (§Этап 9 дорожной карты, вариант A) --
 * тот же приём `EncryptedSharedPreferences`, что и у `TokenStore.kt`
 * (§Этап 6): приватный ключ в `[Interface]` конфига по чувствительности
 * ничем не уступает `access_token` там -- он даёт возможность поднять
 * туннель В ЭТУ сеть от имени этого устройства.
 *
 * Хранит ровно один профиль (как и `TokenStore` -- один ПК за раз, см.
 * её же докстринг) -- текст .conf-файла как есть (см. `rawConfig`) плюс
 * отдельный флаг "включён ли туннель вообще" (`enabled`), не связанный
 * с самим наличием сохранённого конфига: пользователь может импортировать
 * конфиг заранее, но держать VPN выключенным, пока реально не понадобится
 * доступ вне дома (см. TunnelSettingsActivity.kt).
 */
class TunnelConfigStore(context: Context) {

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

    fun loadRawConfig(): String? = prefs.getString(KEY_RAW_CONFIG, null)

    fun saveRawConfig(text: String) {
        prefs.edit().putString(KEY_RAW_CONFIG, text).apply()
    }

    fun clearConfig() {
        prefs.edit().remove(KEY_RAW_CONFIG).putBoolean(KEY_ENABLED, false).apply()
    }

    var enabled: Boolean
        get() = prefs.getBoolean(KEY_ENABLED, false)
        set(value) = prefs.edit().putBoolean(KEY_ENABLED, value).apply()

    companion object {
        private const val PREFS_FILE_NAME = "tunnel_config"
        private const val KEY_RAW_CONFIG = "raw_config"
        private const val KEY_ENABLED = "enabled"
    }
}
