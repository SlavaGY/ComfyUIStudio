package com.comfyuistudio.terminal.sshproxy

import com.comfyuistudio.terminal.pairing.TokenStore
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Автоматическая доставка SSH-профиля с ПК на телефон (этап 3 аудита):
 * при открытии терминала запрашиваем `GET /ssh/config` тем же Bearer-
 * токеном устройства, что и остальной Remote API, и, если версия ключа
 * на сервере отличается от сохранённой, обновляем [SshProxyConfigStore].
 * Ручной ввод в SshProxySettingsActivity остаётся запасным путём.
 *
 * Вызывается из фонового потока ДО решения, поднимать ли туннель (см.
 * TerminalActivity.kt). Любая ошибка сети -- не повод блокировать
 * терминал: возвращаем [Outcome.Failed], а вызывающий код просто
 * продолжает с тем профилем, что уже сохранён.
 */
class SshProfileSync(private val store: SshProxyConfigStore) {

    sealed class Outcome {
        object Updated : Outcome()
        object UpToDate : Outcome()
        object Unavailable : Outcome()
        data class Failed(val message: String) : Outcome()
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(3, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.SECONDS)
        .build()

    fun sync(device: TokenStore.Device): Outcome {
        val url = try {
            HttpUrl.Builder()
                .scheme("http")
                .host(device.host)
                .port(device.port)
                .addPathSegments("api/v1/remote/ssh/config")
                .build()
        } catch (e: IllegalArgumentException) {
            return Outcome.Failed("неверный адрес ПК: ${device.host}")
        }
        val request = Request.Builder()
            .url(url)
            .header("Authorization", "Bearer ${device.accessToken}")
            .get()
            .build()

        val activeClient = SshSocksProxy.proxyOrNull()
            ?.let { client.newBuilder().proxy(it).build() }
            ?: client

        val (code, body) = try {
            activeClient.newCall(request).execute().use { response ->
                response.code to response.body?.string().orEmpty()
            }
        } catch (e: IOException) {
            return Outcome.Failed("не удалось запросить профиль SSH: ${e.message}")
        }
        if (code !in 200..299) {
            return Outcome.Failed("сервер ответил ${code}")
        }

        return when (val parsed = SshConfigParser.parse(body)) {
            is SshConfigResponse.Unavailable -> Outcome.Unavailable
            is SshConfigResponse.Invalid -> Outcome.Failed(parsed.reason)
            is SshConfigResponse.Available -> {
                if (!SshConfigParser.shouldApply(parsed.keyVersion, store.keyVersion)) {
                    Outcome.UpToDate
                } else {
                    store.applyServerProfile(parsed.profile, parsed.keyVersion)
                    if (SshSocksProxy.isRunning()) {
                        SshSocksProxy.disconnect()
                    }
                    Outcome.Updated
                }
            }
        }
    }
}
