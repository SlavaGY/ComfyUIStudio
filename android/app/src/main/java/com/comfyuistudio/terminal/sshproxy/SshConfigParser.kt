package com.comfyuistudio.terminal.sshproxy

import org.json.JSONException
import org.json.JSONObject

/**
 * Разбор ответа `GET /api/v1/remote/ssh/config` (см. routes/ssh_config.py
 * и SshConfigResponse в models.py на сервере). Чистая функция без сети и
 * без Android-хранилищ -- чтобы разбор можно было проверить обычным
 * JVM-тестом (SshConfigParserTest). Ответ содержит приватный ключ,
 * поэтому toString() результатов нигде не печатается и в лог не идёт.
 */
sealed class SshConfigResponse {
    /** Профиль на сервере не настроен (кнопка в Studio ещё не нажималась
     * или у ПК нет адреса для телефона). Профиль на телефоне не трогаем. */
    object Unavailable : SshConfigResponse()

    data class Available(
        val keyVersion: Int,
        val profile: SshProxyConfigStore.Profile,
    ) : SshConfigResponse()

    /** Ответ не по контракту -- профиль не применяем. */
    data class Invalid(val reason: String) : SshConfigResponse()
}

object SshConfigParser {

    fun parse(body: String): SshConfigResponse {
        val json = try {
            JSONObject(body)
        } catch (e: JSONException) {
            return SshConfigResponse.Invalid("ответ сервера не является JSON")
        }
        if (!json.optBoolean("available", false)) {
            return SshConfigResponse.Unavailable
        }
        val keyVersion = json.optInt("key_version", -1)
        if (keyVersion < 1) {
            return SshConfigResponse.Invalid("в ответе нет key_version")
        }
        val host = text(json, "host", trim = true)
            ?: return SshConfigResponse.Invalid("в ответе нет host")
        val username = text(json, "username", trim = true)
            ?: return SshConfigResponse.Invalid("в ответе нет username")
        val key = text(json, "private_key", trim = false)
            ?: return SshConfigResponse.Invalid("в ответе нет private_key")
        val port = json.optInt("ssh_port", -1)
        if (port !in 1..65535) {
            return SshConfigResponse.Invalid("в ответе неверный ssh_port")
        }
        if (!key.contains("PRIVATE KEY")) {
            return SshConfigResponse.Invalid("private_key не похож на приватный ключ")
        }
        val profile = SshProxyConfigStore.Profile(
            host = host,
            port = port,
            username = username,
            privateKeyPem = key.replace("\r\n", "\n"),
            passphrase = null,
        )
        return SshConfigResponse.Available(keyVersion, profile)
    }

    /**
     * Нужно ли применять серверный профиль. Сравниваем на неравенство, а
     * не "новее": если Studio потеряла файл стора (он битый, см.
     * ssh_config_store.py), счётчик на сервере начнётся заново с 1, и
     * строгое "новее" навсегда оставило бы телефон на старом ключе.
     * Ручная правка профиля на телефоне keyVersion не меняет, поэтому
     * повторной перезаписи при каждом подключении не происходит.
     */
    fun shouldApply(serverVersion: Int, storedVersion: Int): Boolean =
        serverVersion != storedVersion

    private fun text(json: JSONObject, name: String, trim: Boolean): String? {
        if (!json.has(name) || json.isNull(name)) return null
        val raw = json.optString(name, "")
        val value = if (trim) raw.trim() else raw
        return if (value.isBlank()) null else value
    }
}
