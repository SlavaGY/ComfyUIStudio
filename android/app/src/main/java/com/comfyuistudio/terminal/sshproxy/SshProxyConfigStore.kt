package com.comfyuistudio.terminal.sshproxy

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Хранилище для варианта B (§Этап 9, SSH-прокси) -- тот же приём
 * `EncryptedSharedPreferences`, что и у `TokenStore.kt`/`TunnelConfigStore.kt`:
 * приватный SSH-ключ по чувствительности не уступает WireGuard-ключу или
 * access_token. Один профиль на приложение (как и у обоих упомянутых
 * хранилищ) -- отдельная сущность от `TunnelConfigStore` (вариант A),
 * т.к. это принципиально другой, независимый механизм — оба могут быть
 * настроены одновременно, но включён (см. [enabled]) на практике должен
 * быть только один (см. `TerminalActivity.kt` про порядок приоритета).
 */
class SshProxyConfigStore(context: Context) {

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

    data class Profile(
        val host: String,
        val port: Int,
        val username: String,
        val privateKeyPem: String,
        val passphrase: String?,
    )

    fun loadProfile(): Profile? {
        val host = prefs.getString(KEY_HOST, null) ?: return null
        val username = prefs.getString(KEY_USERNAME, null) ?: return null
        val key = prefs.getString(KEY_PRIVATE_KEY, null) ?: return null
        val port = prefs.getInt(KEY_PORT, 22)
        val passphrase = prefs.getString(KEY_PASSPHRASE, null)
        return Profile(host, port, username, key, passphrase)
    }

    fun saveProfile(profile: Profile) {
        val editor = prefs.edit()
            .putString(KEY_HOST, profile.host)
            .putInt(KEY_PORT, profile.port)
            .putString(KEY_USERNAME, profile.username)
            .putString(KEY_PRIVATE_KEY, profile.privateKeyPem)
        if (profile.passphrase != null) {
            editor.putString(KEY_PASSPHRASE, profile.passphrase)
        } else {
            editor.remove(KEY_PASSPHRASE)
        }
        editor.apply()
    }

    /** Полный сброс профиля, включая запомненный отпечаток ключа сервера
     * (см. TOFU в SshSocksProxy.kt) -- следующее подключение снова
     * примет ЛЮБОЙ host key как новый первый раз, что и ожидается при
     * замене всего профиля (например, переезд на другой сервер). */
    fun clearConfig() {
        prefs.edit().clear().apply()
    }

    var enabled: Boolean
        get() = prefs.getBoolean(KEY_ENABLED, false)
        set(value) = prefs.edit().putBoolean(KEY_ENABLED, value).apply()

    /** Версия ключа, с которой профиль был получен от Studio
     * (GET /ssh/config, см. SshProfileSync). 0 -- профиль не получали
     * автоматически (пустой или введён вручную). */
    val keyVersion: Int
        get() = prefs.getInt(KEY_KEY_VERSION, 0)

    /**
     * Применяет профиль, полученный от Studio: сохраняет его вместе с
     * версией, сбрасывает запомненный отпечаток ключа сервера (замена
     * профиля -- как "переезд на другой сервер", см. [clearConfig]) и
     * при ПЕРВОМ автоматическом получении ([keyVersion] == 0) включает
     * тумблер -- пользователь сам нажал "Настроить SSH-доступ" в Studio,
     * вводить и включать что-то вручную не нужно. Дальнейшие обновления
     * флаг [enabled] не трогают: если человек выключил SSH, он остаётся
     * выключенным.
     */
    fun applyServerProfile(profile: Profile, serverKeyVersion: Int) {
        val firstAutomaticImport = keyVersion == 0
        val editor = prefs.edit()
            .putString(KEY_HOST, profile.host)
            .putInt(KEY_PORT, profile.port)
            .putString(KEY_USERNAME, profile.username)
            .putString(KEY_PRIVATE_KEY, profile.privateKeyPem)
            .remove(KEY_PASSPHRASE)
            .remove(KEY_HOST_KEY_FP)
            .putInt(KEY_KEY_VERSION, serverKeyVersion)
        if (firstAutomaticImport) {
            editor.putBoolean(KEY_ENABLED, true)
        }
        editor.apply()
    }

    fun loadHostKeyFingerprint(): String? = prefs.getString(KEY_HOST_KEY_FP, null)

    fun saveHostKeyFingerprint(fingerprint: String) {
        prefs.edit().putString(KEY_HOST_KEY_FP, fingerprint).apply()
    }

    companion object {
        private const val PREFS_FILE_NAME = "ssh_proxy_config"
        private const val KEY_HOST = "host"
        private const val KEY_PORT = "port"
        private const val KEY_USERNAME = "username"
        private const val KEY_PRIVATE_KEY = "private_key"
        private const val KEY_PASSPHRASE = "passphrase"
        private const val KEY_ENABLED = "enabled"
        private const val KEY_HOST_KEY_FP = "host_key_fingerprint"
        private const val KEY_KEY_VERSION = "key_version"
    }
}
