package com.comfyuistudio.terminal.sshproxy

import android.util.Base64
import android.util.Log
import com.jcraft.jsch.Channel
import com.jcraft.jsch.ChannelDirectTCPIP
import com.jcraft.jsch.HostKey
import com.jcraft.jsch.HostKeyRepository
import com.jcraft.jsch.JSch
import com.jcraft.jsch.Session
import com.jcraft.jsch.UserInfo
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.security.MessageDigest
import java.security.Security
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import org.bouncycastle.jce.provider.BouncyCastleProvider

private const val TAG = "SshSocksProxy"

/**
 * §Этап 9 дорожной карты, вариант B ("доступ вне дома без поднятия
 * VPN") -- реализован НЕ через userspace WireGuard (см. подробный
 * разбор в дорожной карте про `CAP_NET_ADMIN`/`tsnet`/`libtailscale` --
 * реальное ограничение платформы, не пробел в конкретной библиотеке), а
 * через обычный SSH с динамическим (SOCKS5) проксированием портов --
 * ровно тот же принцип, которым пользуются браузеры со встроенным "VPN"
 * (Opera и т.п. -- на самом деле просто HTTPS-прокси только для самого
 * браузера, без касания системной маршрутизации, см. обсуждение в
 * дорожной карте).
 *
 * Устройство: SSH-сессия (через `com.github.mwiede:jsch`, см. build.
 * gradle.kts) к домашнему ПК (у пользователя уже включён встроенный в
 * Windows OpenSSH-сервер, см. `SSH_Proxy_Server_Setup.md`) + локальный
 * SOCKS5-сервер на 127.0.0.1 внутри ЭТОГО процесса. Готовой библиотеки
 * с SOCKS5 "из коробки" под JSch нет (оригинальный JSch не умеет
 * динамический форвардинг `-D`) -- реализован сам, поверх низкоуровневого
 * примитива `direct-tcpip`-канала (`ChannelDirectTCPIP.setHost/setPort`),
 * который есть в любой SSH-библиотеке.
 *
 * `ProxyController` (см. `TerminalActivity.kt`) затем направляет ТОЛЬКО
 * WebView этого приложения на `127.0.0.1:LOCAL_PORT` -- никакого
 * `VpnService`, никакого системного перехвата трафика, поэтому
 * гарантированно не конфликтует со сторонним VPN пользователя (в
 * отличие от варианта A, см. живой отчёт про Hide.me в дорожной карте).
 *
 * Проверка ключа сервера (host key) -- TOFU (trust-on-first-use):
 * первое подключение принимает любой ключ и запоминает его отпечаток
 * (`SshProxyConfigStore.saveHostKeyFingerprint`), дальнейшие подключения
 * сверяют его -- при несовпадении JSch сам обрывает соединение с
 * ошибкой (см. `TofuHostKeyRepository` ниже). Это заметно безопаснее
 * голого `StrictHostKeyChecking=no` (полностью отключил бы проверку
 * навсегда), хоть и не защищает от подмены именно при самом первом
 * подключении -- обычный компромисс TOFU, тот же, что использует сам
 * OpenSSH по умолчанию при первом коннекте к незнакомому хосту.
 *
 * НЕ ПРОВЕРЕНО вообще ничем, кроме статического анализа -- в этой
 * песочнице нет ни Android-устройства, ни SSH-сервера для живого теста.
 * Особенно стоит перепроверить порядок `connect()`/`getInputStream()` у
 * `ChannelDirectTCPIP` -- по документации API должен быть безопасен в
 * показанном здесь порядке, но не собран и не запущен вживую.
 */
object SshSocksProxy {
    /** 127.0.0.1:этот порт -- локальный SOCKS5, на который затем
     * указывается ProxyController/OkHttp. Порт внутренний, наружу
     * никогда не выставляется (ServerSocket биндится только на
     * loopback, см. connect() ниже) -- конкретное значение не имеет
     * значения, лишь бы не пересекалось с чем-то ещё в самом приложении. */
    const val LOCAL_PORT = 18080

    private var session: Session? = null
    private var serverSocket: ServerSocket? = null
    private val running = AtomicBoolean(false)
    private val executor = Executors.newCachedThreadPool()

    @Volatile
    var lastError: String? = null
        private set

    /**
     * НОВОЕ (живой отчёт: "уведомление не пришло, картинки не
     * скачались" при работе через SSH вне дома) -- см. докстринг
     * `GeneratedImageSaver.kt`/`RemotePairingClient.kt` про то, зачем
     * это нужно: `ProxyController` (см. `TerminalActivity.kt`)
     * заворачивает в SOCKS5 ТОЛЬКО трафик WebView этого процесса --
     * никакой другой сетевой код (в т.ч. свой собственный `OkHttpClient`
     * у скачивания картинок) через него автоматически не идёт и, вне
     * домашней сети, просто не может достучаться до `device.host`
     * (обычный LAN-адрес, недостижимый снаружи напрямую). Возвращает
     * `java.net.Proxy` на локальный SOCKS5, ЕСЛИ туннель сейчас поднят
     * (`isRunning()`), иначе `null` -- вызывающая сторона в этом случае
     * должна использовать прямое подключение (обычный сценарий "телефон
     * дома, SSH вообще выключен").
     */
    fun proxyOrNull(): java.net.Proxy? {
        if (!isRunning()) return null
        return java.net.Proxy(java.net.Proxy.Type.SOCKS, InetSocketAddress("127.0.0.1", LOCAL_PORT))
    }

    init {
        // НАЙДЕННЫЙ БАГ (живая сборка, §Этап 9): без этого -- "Auth
        // cancel for methods 'publickey,password,keyboard-interactive'"
        // при попытке подписать хендшейк ed25519-ключом на обычном
        // Android-рантайме (см. докстринг класса и комментарий у
        // зависимости bcprov-jdk18on в build.gradle.kts). Регистрация
        // должна произойти ДО первого jsch.getSession()/connect() --
        // объект `SshSocksProxy` синглтон, поэтому `init` выполняется
        // ровно один раз на процесс, до вызова connect() ниже.
        // Проверка на null -- на случай, если что-то ещё в процессе уже
        // зарегистрировало провайдер с тем же именем (Security.addProvider
        // с дублирующимся именем no-op молча, но лишний вызов не бесплатен).
        if (Security.getProvider(BouncyCastleProvider.PROVIDER_NAME) == null) {
            Security.addProvider(BouncyCastleProvider())
        }
    }

    fun isRunning(): Boolean = running.get()

    /**
     * Блокирующая (SSH-handshake + bind локального сокета) -- вызывающая
     * сторона (см. `TerminalActivity.kt`) сама уводит на фоновый поток.
     * Бросает исключение при сбое (неверный ключ, сервер недоступен,
     * несовпавший host key и т.п.) -- сообщение кладётся в [lastError]
     * ДО throw, чтобы UI мог показать его даже если сам вызов обёрнут в
     * `try/catch` без доступа к телу исключения.
     */
    @Synchronized
    fun connect(host: String, port: Int, username: String, privateKeyPem: String, passphrase: String?, configStore: SshProxyConfigStore) {
        disconnect()
        lastError = null
        try {
            val jsch = JSch()
            jsch.setHostKeyRepository(TofuHostKeyRepository(configStore))
            jsch.addIdentity(
                "comfyuistudio-ssh",
                privateKeyPem.toByteArray(Charsets.UTF_8),
                null,
                passphrase?.toByteArray(Charsets.UTF_8),
            )

            val newSession = jsch.getSession(username, host, port)
            // "ask" (не "no"!) -- проверка host key ОСТАЁТСЯ включённой,
            // просто на "не видели раньше" сама сессия не спрашивает
            // интерактивно (это фоновый процесс без готового диалога на
            // этот момент), а автоматически принимает через UserInfo
            // ниже -- см. докстринг класса про TOFU.
            newSession.setConfig("StrictHostKeyChecking", "ask")
            newSession.userInfo = AutoAcceptUserInfo()
            newSession.timeout = 15000
            newSession.connect(15000)
            // НАЙДЕННЫЙ БАГ (живой отчёт: "сначала показал интерфейс, потом
            // опять потерял соединение") -- `newSession.timeout` выше не
            // только таймаут самого handshake'а, JSch применяет то же
            // значение как постоянный SO_TIMEOUT фонового потока чтения
            // сессии И ПОСЛЕ подключения. Если за 15 секунд по control-
            // соединению не прошло НИ БАЙТА (а именно так и бывает, когда
            // страница уже загрузилась и просто открыта, без новых
            // запросов) -- фоновый поток JSch получает SocketTimeoutException
            // и без настроенного ServerAlive считает это обрывом связи,
            // рвёт всю сессию целиком (вместе со всеми direct-tcpip
            // каналами) -- то есть именно "сначала работает, потом сама
            // сессия умирает от простоя", а не проблема с конкретным
            // запросом. Лечится штатным для JSch способом: активные
            // keepalive-пакеты значительно чаще самого таймаута, чтобы
            // держать control-соединение "живым" трафиком даже когда
            // пользователь просто смотрит на уже загруженную страницу.
            newSession.serverAliveInterval = 10_000
            newSession.serverAliveCountMax = 3
            session = newSession

            val socket = ServerSocket()
            socket.reuseAddress = true
            socket.bind(InetSocketAddress("127.0.0.1", LOCAL_PORT))
            serverSocket = socket
            running.set(true)

            executor.submit { acceptLoop(socket) }
        } catch (e: Exception) {
            lastError = e.message
            disconnect()
            throw e
        }
    }

    /** Безопасно вызывать повторно и когда уже отключено. */
    @Synchronized
    fun disconnect() {
        running.set(false)
        try {
            serverSocket?.close()
        } catch (e: IOException) {
            // Сокет мог быть уже закрыт -- не повод падать при очистке.
        }
        serverSocket = null
        try {
            session?.disconnect()
        } catch (e: Exception) {
            Log.w(TAG, "Отключение SSH-сессии прошло с предупреждением: ${e.message}")
        }
        session = null
    }

    private fun acceptLoop(server: ServerSocket) {
        while (running.get()) {
            val client = try {
                server.accept()
            } catch (e: IOException) {
                break // сокет закрыт через disconnect() -- штатное завершение цикла
            }
            executor.submit { handleClient(client) }
        }
    }

    /** Разбор одного SOCKS5-запроса (RFC 1928, только команда CONNECT --
     * ровно то, что использует WebView/OkHttp) и проброс через
     * `direct-tcpip`-канал текущей SSH-сессии. */
    private fun handleClient(client: Socket) {
        try {
            // 15с -- ТОЛЬКО на разбор самого SOCKS5-приветствия/запроса
            // ниже (несколько байт, должны прийти почти сразу) -- см.
            // ниже, почему это ОБЯЗАТЕЛЬНО снимается перед проксированием
            // данных.
            client.soTimeout = 15000
            val input = client.getInputStream()
            val output = client.getOutputStream()

            // --- приветствие: VER=5, NMETHODS, METHODS[] ---
            val ver = input.read()
            if (ver != 5) {
                client.close()
                return
            }
            val nMethods = input.read()
            val methods = ByteArray(nMethods)
            readFully(input, methods)
            // Без аутентификации (METHOD=0x00) -- сокет слушает только
            // 127.0.0.1, снаружи процесса физически недостижим.
            output.write(byteArrayOf(5, 0))
            output.flush()

            // --- запрос: VER, CMD, RSV, ATYP, DST.ADDR, DST.PORT ---
            val reqVer = input.read()
            val cmd = input.read()
            input.read() // RSV, зарезервирован, игнорируется
            val atyp = input.read()
            if (reqVer != 5 || cmd != 1) {
                sendSocksReply(output, 7) // command not supported
                client.close()
                return
            }
            val destHost = when (atyp) {
                1 -> { // IPv4
                    val addr = ByteArray(4)
                    readFully(input, addr)
                    InetAddress.getByAddress(addr).hostAddress
                }
                3 -> { // доменное имя
                    val len = input.read()
                    val nameBytes = ByteArray(len)
                    readFully(input, nameBytes)
                    String(nameBytes, Charsets.US_ASCII)
                }
                4 -> { // IPv6 -- не ожидается в нашем сценарии (device.host
                    // из TokenStore всегда IPv4), но разбираем корректно
                    // на случай, если WebView всё же его пришлёт.
                    val addr = ByteArray(16)
                    readFully(input, addr)
                    InetAddress.getByAddress(addr).hostAddress
                }
                else -> {
                    sendSocksReply(output, 8) // address type not supported
                    client.close()
                    return
                }
            }
            val portBytes = ByteArray(2)
            readFully(input, portBytes)
            val destPort = ((portBytes[0].toInt() and 0xFF) shl 8) or (portBytes[1].toInt() and 0xFF)

            val currentSession = session
            if (currentSession == null || !currentSession.isConnected) {
                sendSocksReply(output, 1) // general failure
                client.close()
                return
            }

            val channel = currentSession.openChannel("direct-tcpip") as ChannelDirectTCPIP
            channel.setHost(destHost)
            channel.setPort(destPort)
            try {
                channel.connect(10000)
            } catch (e: Exception) {
                Log.w(TAG, "direct-tcpip до $destHost:$destPort не удался: ${e.message}")
                sendSocksReply(output, 4) // host unreachable
                client.close()
                return
            }

            sendSocksReply(output, 0) // succeeded
            // НАЙДЕННЫЙ БАГ (живой отчёт: "в меню выбора приложений
            // частенько рвётся соединение... в режиме Imagine такого не
            // было") -- 15-секундный soTimeout выше был выставлен на весь
            // срок жизни сокета, включая САМУ передачу данных ниже, а не
            // только на разбор SOCKS-запроса. Главная страница (список
            // апп) — статичный HTML, после загрузки которого WebView
            // держит keep-alive-соединение открытым в ПРОСТОЕ в ожидании
            // возможного переиспользования, но НИЧЕГО через него не
            // передаёт, пока пользователь просто смотрит на список --
            // 15 секунд такой тишины, и pipe() ниже получает
            // SocketTimeoutException на блокирующем read(), тихо (см. её
            // же catch) закрывает ИМЕННО ЭТУ сторону туннеля -- а WebView
            // при следующей навигации пытается переиспользовать уже
            // мёртвый с нашей стороны сокет и получает обрыв. Imagine
            // же держит один активный WebSocket с постоянным обменом
            // сообщениями (статус очереди и т.п.) -- там 15 секунд полной
            // тишины попросту не бывает, поэтому баг там не проявлялся.
            // 0 (бесконечный таймаут) -- нормальная семантика keep-alive
            // HTTP-соединения: сама по себе SSH-сессия уже поддерживается
            // отдельным keepalive (см. serverAliveInterval в connect()
            // выше), а если direct-tcpip канал всё же умрёт, read() здесь
            // получит обычный IOException от самого JSch, а не таймаут --
            // отдельный сторож по времени для уже установленного туннеля
            // не нужен.
            client.soTimeout = 0
            pumpBidirectional(input, output, channel)
        } catch (e: Exception) {
            Log.w(TAG, "Ошибка SOCKS-соединения: ${e.message}")
        } finally {
            try {
                client.close()
            } catch (e: IOException) {
                // Уже закрыт -- не повод логировать как ошибку.
            }
        }
    }

    private fun pumpBidirectional(localIn: InputStream, localOut: OutputStream, channel: Channel) {
        val channelIn = channel.inputStream
        val channelOut = channel.outputStream
        val toChannel = executor.submit { pipe(localIn, channelOut) }
        val toLocal = executor.submit { pipe(channelIn, localOut) }
        // Ждём завершения ОБОИХ направлений (любая сторона может
        // закрыться первой -- это нормальное завершение передачи, не
        // ошибка, см. pipe() ниже) прежде чем закрыть канал и вернуться
        // в handleClient(), которая закроет клиентский сокет в finally.
        toChannel.get()
        toLocal.get()
        try {
            channel.disconnect()
        } catch (e: Exception) {
            // Канал мог быть уже закрыт удалённой стороной.
        }
    }

    private fun sendSocksReply(output: OutputStream, rep: Int) {
        // BND.ADDR/BND.PORT нулями -- клиенты (WebView/OkHttp) их не
        // используют при обычном CONNECT, значение не имеет значения.
        output.write(byteArrayOf(5, rep.toByte(), 0, 1, 0, 0, 0, 0, 0, 0))
        output.flush()
    }

    private fun readFully(input: InputStream, buffer: ByteArray) {
        var offset = 0
        while (offset < buffer.size) {
            val read = input.read(buffer, offset, buffer.size - offset)
            if (read == -1) throw IOException("Соединение закрыто во время чтения SOCKS-запроса")
            offset += read
        }
    }

    private fun pipe(from: InputStream, to: OutputStream) {
        val buffer = ByteArray(8192)
        try {
            while (true) {
                val read = from.read(buffer)
                if (read == -1) break
                to.write(buffer, 0, read)
                to.flush()
            }
        } catch (e: IOException) {
            // Один конец закрылся раньше другого -- обычное завершение
            // передачи данных, а не ошибка, специально не логируется.
        }
    }
}

/** См. докстринг [SshSocksProxy] про TOFU. Хранит и сверяет отпечаток
 * (SHA-256 от сырых байт ключа) через [SshProxyConfigStore] -- не файл
 * known_hosts, т.к. профиль всё равно один на приложение. */
private class TofuHostKeyRepository(private val store: SshProxyConfigStore) : HostKeyRepository {

    override fun check(host: String?, key: ByteArray?): Int {
        if (key == null) return HostKeyRepository.NOT_INCLUDED
        val saved = store.loadHostKeyFingerprint() ?: return HostKeyRepository.NOT_INCLUDED
        return if (saved == fingerprint(key)) HostKeyRepository.OK else HostKeyRepository.CHANGED
    }

    override fun add(hostkey: HostKey, ui: UserInfo?) {
        val keyBytes = Base64.decode(hostkey.key, Base64.DEFAULT)
        store.saveHostKeyFingerprint(fingerprint(keyBytes))
    }

    override fun remove(host: String?, type: String?) {
        // Один профиль на приложение -- удаление конкретного хоста не
        // отличимо от полного сброса; делает clearConfig() в самом
        // сторе, здесь оставлено пустым (JSch вызывает это крайне редко
        // и не в нашем сценарии TOFU-без-интерактива).
    }

    override fun remove(host: String?, type: String?, key: ByteArray?) {
        // См. remove(host, type) выше.
    }

    override fun getKnownHostsRepositoryID(): String = "comfyuistudio-tofu"

    override fun getHostKey(): Array<HostKey> = emptyArray()

    override fun getHostKey(host: String?, type: String?): Array<HostKey> = emptyArray()

    private fun fingerprint(keyBytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(keyBytes)
        return Base64.encodeToString(digest, Base64.NO_WRAP)
    }
}

/** Автоматически подтверждает "незнакомый host key" при первом
 * подключении (см. TOFU в докстринге [SshSocksProxy]) -- дальше решение
 * принимает уже [TofuHostKeyRepository.check] по сохранённому отпечатку,
 * без участия этого класса. Аутентификация -- по ключу (см.
 * `JSch.addIdentity` в [SshSocksProxy.connect]), поэтому методы,
 * связанные с паролем, никогда не должны вызываться на практике;
 * возвращают null/false на случай, если всё же будут вызваны, вместо
 * падения. */
private class AutoAcceptUserInfo : UserInfo {
    override fun getPassphrase(): String? = null
    override fun getPassword(): String? = null
    override fun promptPassword(message: String?): Boolean = false
    override fun promptPassphrase(message: String?): Boolean = false
    override fun promptYesNo(message: String?): Boolean = true
    override fun showMessage(message: String?) {
        Log.i(TAG, "JSch: $message")
    }
}
