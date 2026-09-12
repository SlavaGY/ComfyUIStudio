package com.comfyuistudio.terminal.tunnel

import android.content.Context
import android.content.Intent
import android.util.Log
import com.wireguard.android.backend.GoBackend
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayInputStream
import java.nio.charset.StandardCharsets

private const val TAG = "TunnelManager"

// Имя туннеля внутри самой библиотеки (её собственный "Tunnel.getName()") --
// приложению принадлежит ровно один профиль (см. TunnelConfigStore), имя
// произвольное и наружу нигде не показывается.
private const val TUNNEL_NAME = "comfyuistudio"

/**
 * TunnelProvider (§Этап 9 дорожной карты, вариант A) -- точка абстракции
 * поверх способа соединения с Remote-сервером, за которой прячется
 * `com.wireguard.android.backend.GoBackend`, чтобы остальной код
 * терминала (TerminalActivity, pairing) не знал, что вообще существует
 * VPN -- см. докстринг интерфейса в самом роадмапе. Будущий вариант B
 * (userspace WG + локальный HTTP-прокси) реализуется как ВТОРОЙ класс с
 * тем же интерфейсом, без переделки вызывающего кода.
 *
 * Согласие пользователя на VPN (`VpnService.prepare`) -- системный
 * Activity-флоу, поэтому НЕ спрятано внутри `connect()` (см. её
 * докстринг): экран, который хочет поднять туннель, сам обязан сначала
 * проверить [prepareIntent] и, если не null, показать пользователю
 * системный диалог через `ActivityResultContracts.StartActivityForResult`
 * -- и только потом звать [connect]. Это единственное место, где
 * реализация варианта A протекает наружу интерфейса -- у чистого
 * `TunnelProvider` такого метода в принципе не было бы, но без него
 * Android не даст поднять VPN вообще.
 */
interface TunnelProvider {
    /** Intent для системного диалога согласия, или null, если согласие
     * уже когда-то было дано (или это не первый запуск туннеля). */
    fun prepareIntent(context: Context): Intent?

    /** Поднимает туннель с данным конфигом. Бросает исключение при сбое
     * (неверный конфиг, сервер не отвечает на handshake и т.п.) --
     * вызывающая сторона сама решает, как показать это пользователю
     * (см. TunnelSettingsActivity.kt). */
    suspend fun connect(context: Context, config: Config)

    /** Гасит туннель, если он был поднят; безопасно вызывать и когда он
     * уже выключен. */
    suspend fun disconnect()

    fun isConnected(): Boolean
}

/**
 * Единственная реализация на сейчас (вариант A из роадмапа) --
 * `GoBackend`, сам класс параллельно реализует интерфейс `Tunnel` самой
 * библиотеки (имя + колбэк смены состояния), т.к. `Backend.setState()`
 * требует объект `Tunnel` на каждый вызов, а держать отдельный
 * анонимный класс под один-единственный профиль этого приложения смысла
 * нет.
 */
object VpnServiceTunnelProvider : TunnelProvider, Tunnel {

    @Volatile
    private var state: Tunnel.State = Tunnel.State.DOWN

    private var backend: GoBackend? = null

    private fun backendFor(context: Context): GoBackend =
        backend ?: GoBackend(context.applicationContext).also { backend = it }

    override fun prepareIntent(context: Context): Intent? =
        GoBackend.VpnService.prepare(context.applicationContext)

    override suspend fun connect(context: Context, config: Config) {
        withContext(Dispatchers.IO) {
            // setState -- блокирующий синхронный вызов (сам поднимает
            // wireguard-go, настраивает VpnService.Builder и т.п.), см.
            // тот же приём withContext(Dispatchers.IO) в официальном
            // TunnelManager.kt самой библиотеки (см. её исходники) --
            // не наша придумка, а задокументированный паттерн
            // использования Backend.setState().
            state = backendFor(context).setState(this@VpnServiceTunnelProvider, Tunnel.State.UP, config)
        }
    }

    override suspend fun disconnect() {
        val currentBackend = backend ?: return
        withContext(Dispatchers.IO) {
            try {
                state = currentBackend.setState(this@VpnServiceTunnelProvider, Tunnel.State.DOWN, null)
            } catch (e: Exception) {
                // Гасить туннель, которого, возможно, уже и не было
                // (например, повторный disconnect()) -- не повод падать,
                // это фоновая операция очистки, не пользовательское
                // действие с ожидаемым результатом.
                Log.w(TAG, "Не удалось корректно остановить туннель: ${e.message}")
                state = Tunnel.State.DOWN
            }
        }
    }

    override fun isConnected(): Boolean = state == Tunnel.State.UP

    // --- com.wireguard.android.backend.Tunnel ---

    override fun getName(): String = TUNNEL_NAME

    override fun onStateChange(newState: Tunnel.State) {
        state = newState
    }
}

/**
 * Разбор текста .conf-файла (wg-quick формат, включая нестандартное для
 * "чистого" wg, но понятное именно этой Android-библиотеке поле
 * `IncludedApplications` под `[Interface]` -- см. TunnelSettingsActivity.kt
 * про то, зачем оно нужно и что туда вписывать) в объект `Config`.
 * Бросает `com.wireguard.config.BadConfigException` на невалидный текст
 * -- вызывающая сторона сама решает, как показать ошибку.
 */
fun parseTunnelConfig(rawText: String): Config =
    Config.parse(ByteArrayInputStream(rawText.toByteArray(StandardCharsets.UTF_8)))
