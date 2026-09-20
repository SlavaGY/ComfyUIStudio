package com.comfyuistudio.terminal.terminal

import android.annotation.SuppressLint
import com.comfyuistudio.terminal.BuildConfig
import android.content.Intent
import android.os.Bundle
import android.webkit.CookieManager
import android.webkit.WebView
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.comfyuistudio.terminal.fcm.syncFcmTokenIfPaired
import com.comfyuistudio.terminal.pairing.TokenStore
import com.comfyuistudio.terminal.sshproxy.SshProxyConfigStore
import com.comfyuistudio.terminal.sshproxy.SshProxySettingsActivity
import com.comfyuistudio.terminal.sshproxy.SshSocksProxy
import com.comfyuistudio.terminal.sshproxy.SshTunnelService
import com.comfyuistudio.terminal.tunnel.TunnelConfigStore
import com.comfyuistudio.terminal.tunnel.TunnelSettingsActivity
import com.comfyuistudio.terminal.tunnel.VpnServiceTunnelProvider
import com.comfyuistudio.terminal.tunnel.parseTunnelConfig
import androidx.webkit.ProxyConfig
import androidx.webkit.ProxyController
import androidx.webkit.WebViewFeature
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlin.concurrent.thread

// Порог, после которого ожидание загрузки главного документа
// считается "скорее всего сервер недоступен", а не просто "медленно
// грузится" -- см. докстринг про loadGeneration в TerminalWebView.
// 15с выбрано с запасом поверх обычной загрузки Imagine после
// исправления блокирующих багов прокси (секунды даже на холодном
// кэше), но заметно короче многоминутного таймаута по умолчанию у ОС
// на недостижимый в другой сети LAN-адрес.
private const val LOAD_TIMEOUT_MS = 15_000L

/**
 * `terminal/TerminalActivity.kt` — §Этап 6 дорожной карты: "хост
 * WebView". Всё, что видно после экрана Pairing, отрисовывается
 * сервером (`GET /` из routes/home.py — список плиток "апп", сейчас
 * только Imagine) — эта Activity не знает про Imagine/очередь/прогресс
 * ничего, только показывает URL и обрабатывает "сервер недоступен".
 *
 * WebView, а не Compose — здесь намеренно НЕ AndroidView-обёртка вокруг
 * Compose-контента, а обычный android.webkit.WebView, потому что сам
 * контент — чужая, уже готовая веб-страница сервера, а не что-то,
 * рисуемое этим приложением (см. §0.3: "телефон... тонкий терминал,
 * отображающий интерфейс, целиком переданный сервером").
 */
class TerminalActivity : ComponentActivity() {

    companion object {
        // §Этап 6.5 -- FcmService.kt кладёт сюда путь конкретного апп
        // (сейчас всегда "/apps/imagine/"), чтобы тап по push-
        // уведомлению открывал результат генерации напрямую, а не
        // домашнюю страницу со списком плиток (см. §Этап 6.5, п.3:
        // "по тапу открывает TerminalActivity сразу на нужном апп").
        const val EXTRA_OPEN_PATH = "open_path"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val tokenStore = TokenStore(this)
        val device = tokenStore.load()
        val openPath = intent.getStringExtra(EXTRA_OPEN_PATH)

        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    if (device == null) {
                        // Токен пропал между роутингом MainActivity и этим
                        // экраном (маловероятно, но не должно приводить к
                        // краху) -- откатываемся на Pairing тем же путём,
                        // что и кнопка "Сменить ПК".
                        LaunchedEffect(Unit) {
                            finish()
                        }
                    } else {
                        // НОВОЕ (живой баг, §Этап 6.5) -- досылаем текущий
                        // FCM-токен на сервер при КАЖДОМ открытии терминала,
                        // не только во время pairing (см. докстринг
                        // fcm/FcmTokenUtils.kt::syncFcmTokenIfPaired):
                        // устройства, сопряжённые ДО того, как в проекте
                        // появился google-services.json/сервис-аккаунт,
                        // иначе никогда не отправили бы токен вообще --
                        // pairing происходит только один раз, а
                        // MainActivity при повторном запуске уходит сразу
                        // сюда, минуя экран Pairing целиком. Фоновая
                        // корутина, не блокирует и не задерживает
                        // отрисовку WebView ниже.
                        LaunchedEffect(device) {
                            syncFcmTokenIfPaired(device)
                        }
                        TerminalScreen(device, openPath)
                    }
                }
            }
        }
    }
}

@Composable
private fun TerminalScreen(device: TokenStore.Device, openPath: String?) {
    val context = androidx.compose.ui.platform.LocalContext.current

    // §Этап 9 -- ДВА независимых механизма доступа вне дома, у каждого
    // свой отдельный флаг "включён" (см. докстринг SshProxyConfigStore
    // про то, что оба МОГУТ быть настроены одновременно). Приоритет --
    // SSH (вариант B): если включён, используется он, VPN (вариант A)
    // даже не проверяется -- по прямой просьбе пользователя после
    // живого конфликта с Hide.me (см. дорожную карту), SSH теперь
    // предпочтительный путь. Если SSH выключен -- поведение ровно то
    // же, что было раньше (VPN, если включён, иначе прямое
    // подключение) -- см. комментарии внутри блоков ниже про
    // "ничего не меняет для тех, кто не настраивал X".
    val sshStore = remember { SshProxyConfigStore(context) }
    val tunnelStore = remember { TunnelConfigStore(context) }
    var accessReady by remember { mutableStateOf(!sshStore.enabled && !tunnelStore.enabled) }
    var accessError by remember { mutableStateOf<String?>(null) }
    var pendingPermissionConfig by remember { mutableStateOf<com.wireguard.config.Config?>(null) }

    val vpnPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        val config = pendingPermissionConfig
        pendingPermissionConfig = null
        if (config == null) return@rememberLauncherForActivityResult
        if (result.resultCode == android.app.Activity.RESULT_OK) {
            thread {
                try {
                    runBlocking { VpnServiceTunnelProvider.connect(context, config) }
                } catch (e: Exception) {
                    accessError = "VPN: ${e.message}"
                } finally {
                    accessReady = true
                }
            }
        } else {
            accessError = "Нет согласия на VPN -- пробуем без туннеля."
            accessReady = true
        }
    }

    LaunchedEffect(Unit) {
        if (sshStore.enabled) {
            // Вариант B -- см. докстринг SshSocksProxy.kt целиком.
            // Поднимаем SSH-сессию + локальный SOCKS5, ЗАТЕМ направляем
            // на него ТОЛЬКО WebView этого приложения через
            // ProxyController -- никакого VpnService на этом пути
            // вообще (см. её же докстринг про то, почему это не
            // конфликтует с Hide.me).
            val profile = sshStore.loadProfile()
            if (profile == null) {
                accessReady = true
                return@LaunchedEffect
            }
            try {
                // withContext(IO) -- сам опрос ниже блокирующий (delay в
                // цикле), а не долгий ввод-вывод, но остаётся на IO, чтобы
                // не занимать Main-диспетчер ожиданием. ProxyController
                // ниже снова выполняется на исходном диспетчере
                // LaunchedEffect (Main) после возврата из withContext --
                // ОБЯЗАТЕЛЬНО с главного потока (недопустимо для
                // WebView-API).
                //
                // НОВОЕ -- см. докстринг SshTunnelService.kt: само
                // подключение (SshSocksProxy.connect) теперь происходит
                // ВНУТРИ foreground-сервиса, а не прямо здесь -- иначе
                // туннель падал при сворачивании терминала (живой отчёт:
                // "уведомление не пришло, картинки не скачались", см. её
                // же докстринг про причину). Раньше здесь был прямой
                // блокирующий вызов SshSocksProxy.connect(...); теперь
                // этот блок только просит сервис подняться и ждёт
                // результата опросом (у Service нет колбэка обратно в
                // Activity) -- сам факт подключения по-прежнему отражается
                // в тех же SshSocksProxy.isRunning()/lastError, что и
                // раньше.
                withContext(Dispatchers.IO) {
                    SshTunnelService.start(context)
                    val deadline = System.currentTimeMillis() + 15_000L
                    while (!SshSocksProxy.isRunning() &&
                        SshSocksProxy.lastError == null &&
                        System.currentTimeMillis() < deadline
                    ) {
                        delay(200)
                    }
                    if (!SshSocksProxy.isRunning()) {
                        throw IllegalStateException(
                            SshSocksProxy.lastError ?: "не удалось подключиться за 15 секунд",
                        )
                    }
                }
                if (WebViewFeature.isFeatureSupported(WebViewFeature.PROXY_OVERRIDE)) {
                    val proxyConfig = ProxyConfig.Builder()
                        .addProxyRule("socks5://127.0.0.1:${SshSocksProxy.LOCAL_PORT}")
                        .build()
                    ProxyController.getInstance().setProxyOverride(
                        proxyConfig,
                        { r -> r.run() },
                        { accessReady = true },
                    )
                } else {
                    // Само SSH-соединение поднято, но перенаправить
                    // именно WebView на него нечем -- на этом
                    // устройстве/версии системного WebView нет
                    // ProxyController. Не должно происходить на
                    // современных устройствах (фича давно стабильна), но
                    // лучше явно сообщить, чем молча продолжить без
                    // прокси.
                    accessError = "WebView на этом устройстве не поддерживает ProxyOverride"
                    accessReady = true
                }
            } catch (e: Exception) {
                accessError = "SSH: ${e.message}"
                accessReady = true
            }
            return@LaunchedEffect
        }

        if (!tunnelStore.enabled) return@LaunchedEffect
        val raw = tunnelStore.loadRawConfig()
        if (raw == null) {
            // Включено, но конфиг почему-то не сохранён (не должно
            // случаться через обычный UI TunnelSettingsActivity, но не
            // повод блокировать сам терминал, если всё же произошло).
            accessReady = true
            return@LaunchedEffect
        }
        try {
            val config = parseTunnelConfig(raw)
            val intent = VpnServiceTunnelProvider.prepareIntent(context)
            if (intent != null) {
                pendingPermissionConfig = config
                vpnPermissionLauncher.launch(intent)
            } else {
                VpnServiceTunnelProvider.connect(context, config)
                accessReady = true
            }
        } catch (e: Exception) {
            // Не удалось поднять туннель (битый конфиг, сервер не
            // ответил на handshake и т.п.) -- НЕ блокируем терминал
            // насовсем: если пользователь на самом деле дома, обычный
            // LAN-путь ниже (TerminalWebView) отработает и без VPN как
            // прежде, а если нет -- увидит уже привычный
            // ConnectionErrorOverlay из TerminalWebView, просто без
            // помощи VPN в этой попытке.
            accessError = "VPN: ${e.message}"
            accessReady = true
        }
    }

    // Гасим то, что подняли, при уходе с экрана терминала -- НО только
    // для варианта VPN (WireGuard). Вариант SSH (см. докстринг
    // SshTunnelService.kt) сознательно НЕ гасится здесь -- туннель
    // теперь живёт в foreground-сервисе именно для того, чтобы survive
    // сворачивание терминала (живой отчёт: "уведомление не пришло,
    // картинки не скачались" -- см. её же докстринг про причину).
    // ProxyController -- на главном потоке (onDispose сам по себе уже
    // выполняется на главном потоке Compose) -- сбрасываем override
    // WebView этого экрана в любом случае: он Activity-scoped и дёшево
    // переприменяется заново при следующем открытии терминала, а сам
    // туннель за ним продолжает жить в SshTunnelService.
    DisposableEffect(Unit) {
        onDispose {
            if (sshStore.enabled) {
                if (WebViewFeature.isFeatureSupported(WebViewFeature.PROXY_OVERRIDE)) {
                    ProxyController.getInstance().clearProxyOverride({ r -> r.run() }, {})
                }
            } else if (tunnelStore.enabled) {
                thread { runBlocking { VpnServiceTunnelProvider.disconnect() } }
            }
        }
    }

    Box(modifier = Modifier.fillMaxSize()) {
        if (!accessReady) {
            Column(
                modifier = Modifier.fillMaxSize().padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = androidx.compose.foundation.layout.Arrangement.Center,
            ) {
                CircularProgressIndicator()
                androidx.compose.foundation.layout.Spacer(modifier = Modifier.padding(4.dp))
                Text(if (sshStore.enabled) "Подключение по SSH…" else "Подключение к VPN…")
            }
        } else {
            TerminalWebView(device, openPath)
            accessError?.let {
                // Ненавязчивое уведомление поверх WebView -- не
                // блокирует терминал (см. комментарии выше про то, что
                // сбой туннеля/прокси не должен мешать обычной работе
                // дома).
                Text(
                    it,
                    modifier = Modifier
                        .align(Alignment.TopCenter)
                        .padding(8.dp),
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }

        Row(
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(12.dp),
            horizontalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(8.dp),
        ) {
            OutlinedButton(onClick = { context.startActivity(Intent(context, SshProxySettingsActivity::class.java)) }) {
                Text("SSH")
            }
            OutlinedButton(onClick = { context.startActivity(Intent(context, TunnelSettingsActivity::class.java)) }) {
                Text("VPN")
            }
        }
    }
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun TerminalWebView(device: TokenStore.Device, openPath: String?) {
    val context = androidx.compose.ui.platform.LocalContext.current
    var connectionError by remember { mutableStateOf(false) }
    var reloadTrigger by remember { mutableStateOf(0) }
    // НОВОЕ (просьба "кнопка назад должна выходить к списку апп", а не
    // сразу закрывать приложение целиком) -- ссылка на сам WebView, чтобы
    // BackHandler ниже мог явно перевести его на домашнюю страницу
    // (webView.goBack() тут не подошёл бы: если экран открыт по тапу на
    // push-уведомление (openPath, см. FcmService.kt), домашняя страница
    // вообще ни разу не попадала в историю ЭТОЙ WebView-сессии -- goBack()
    // тогда был бы недоступен либо ушёл бы совсем не туда).
    var webViewRef by remember { mutableStateOf<WebView?>(null) }
    // Путь текущей загруженной страницы ("/" -- список апп, что угодно
    // ещё -- конкретное апп) -- обновляется в onLoadStarted, см. её же
    // докстринг в AuthWebViewClient.kt.
    var isHomePage by remember { mutableStateOf(openPath == null) }

    // НОВОЕ (живой отчёт: "Imagine уходил в загрузку на 2 минуты" +
    // просьба "добавить кнопку перезагрузку, когда приложение не
    // находит ничего по той ссылке" -- обычно "забыл подключиться к
    // общему Wi-Fi/запустить Studio"): [ConnectionErrorOverlay] ниже
    // технически уже существовал и уже умеет "Повторить"/"Сменить ПК" --
    // но раньше показывался ТОЛЬКО по [AuthWebViewClient.onReceivedError],
    // а тот сам стреляет только на явную, быструю сетевую ошибку. Если
    // телефон просто в другой сети, чем ПК, соединение не завершается
    // ошибкой быстро -- ОС пытается достучаться и только спустя СВОЙ
    // собственный, куда более долгий таймаут по умолчанию (минуты), сама
    // в итоге вызовет onReceivedError -- и всё это время пользователь
    // просто смотрит на пустой/висящий WebView, не понимая, реальная это
    // проблема или страница просто медленно грузится.
    //
    // loadGeneration растёт на КАЖДЫЙ старт навигации главного документа
    // (см. AuthWebViewClient.onLoadStarted) -- и на самую первую загрузку
    // из update{} ниже, и на любую последующую (переход по ссылке ВНУТРИ
    // WebView, например с домашней плитки на Imagine). LaunchedEffect
    // ниже пересоздаётся Compose'ом при каждом таком росте (старый
    // таймер автоматически отменяется) -- если за LOAD_TIMEOUT_MS так и
    // не пришёл ни onPageFinished (см. loadCompleted), ни onReceivedError
    // для этой конкретной навигации, сами показываем тот же оверлей, не
    // дожидаясь куда более долгого таймаута самой ОС.
    var loadGeneration by remember { mutableStateOf(0) }
    var loadCompleted by remember { mutableStateOf(false) }

    LaunchedEffect(loadGeneration) {
        loadCompleted = false
        delay(LOAD_TIMEOUT_MS)
        if (!loadCompleted) {
            connectionError = true
        }
    }

    val baseUrl = "http://${device.host}:${device.port}${openPath ?: "/"}"
    // ?token=/&token= -- см. ниже: openPath (из EXTRA_OPEN_PATH) может
    // уже содержать свой query (?prompt_id=..., см. FcmService.kt) --
    // разделитель для токена подбирается по факту, а не жёстко "?",
    // иначе получился бы битый URL с двумя "?" подряд.
    val tokenSeparator = if (baseUrl.contains('?')) '&' else '?'
    // Домашняя ("список апп") -- без openPath и без ?token=, кука уже
    // закреплена после самой первой загрузки (см. комментарий у
    // update{} ниже про "?token= только в первом запросе").
    val homeUrl = "http://${device.host}:${device.port}/"

    // НОВОЕ (просьба "кнопка назад должна выходить к списку апп") --
    // enabled только пока реально показан WebView НЕ на домашней
    // странице; если уже на ней -- не перехватываем вообще, системный
    // back ведёт себя как раньше (закрывает терминал). Пока показан
    // ConnectionErrorOverlay (connectionError=true) тоже не перехватываем
    // -- там кнопка "назад" должна закрывать экран, а не пытаться
    // куда-то грузить всё ещё недоступный сервер.
    androidx.activity.compose.BackHandler(enabled = !connectionError && !isHomePage) {
        webViewRef?.loadUrl(homeUrl)
    }

    if (connectionError) {
        ConnectionErrorOverlay(
            host = "${device.host}:${device.port}",
            onRetry = {
                connectionError = false
                reloadTrigger++
                // loadGeneration НЕ трогаем здесь вручную -- после
                // reloadTrigger++ AndroidView.factory ниже пересоздаст
                // WebView и вызовет loadUrl() заново, что само по себе
                // приведёт к onPageStarted -> onLoadStarted -> росту
                // loadGeneration и новому таймауту, без гонки с ещё не
                // отменённым старым.
            },
            onChangeServer = {
                TokenStore(context).clear()
                (context as? android.app.Activity)?.finish()
            },
        )
    } else {
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = { ctx ->
                // НОВОЕ (живой отчёт: третья попытка вслепую понять,
                // почему кнопка "молчит", логи -- сплошной системный шум
                // без единой строчки про наше приложение) -- включает
                // chrome://inspect на ПК (тот же USB-кабель/ADB, что и
                // logcat): подключить телефон, открыть chrome://inspect
                // в Chrome на ПК, найти там WebView этого приложения --
                // и будет виден настоящий JS-консоль/сетевые вкладки,
                // а не наши догадки по логам, где WebView в принципе
                // ничего не пишет наружу без явного onConsoleMessage
                // (см. WebChromeClient ниже -- он же дублирует ошибки в
                // logcat на случай, если инспектировать через ПК
                // неудобно прямо сейчас). ДОЛЖНО вызываться до создания
                // самого WebView -- то, чем и является этот factory-блок.
                WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
                WebView(ctx).apply {
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    // Куки, включая наш remote_token, должны переживать
                    // перезапуск приложения -- см. AuthWebViewClient
                    // докстринг про cookie-based auth "по факту".
                    CookieManager.getInstance().setAcceptCookie(true)
                    CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)

                    webViewClient = AuthWebViewClient(
                        onConnectionError = { connectionError = true },
                        onPageLoaded = {
                            connectionError = false
                            loadCompleted = true
                        },
                        onLoadStarted = { url ->
                            loadGeneration++
                            // "/" и "" (пустой путь -- бывает у голого
                            // "http://host:port" без хвостового слэша)
                            // оба считаются домашней страницей.
                            val path = url?.let { android.net.Uri.parse(it).path }
                            isHomePage = path.isNullOrEmpty() || path == "/"
                        },
                    )
                    // См. комментарий у setWebContentsDebuggingEnabled
                    // выше -- дублирует console.log/console.error из
                    // самой страницы (в т.ч. из stopServer()/startApp())
                    // прямо в logcat под тегом "WebViewConsole", БЕЗ
                    // необходимости подключать телефон к ПК ради
                    // chrome://inspect каждый раз.
                    webChromeClient = object : android.webkit.WebChromeClient() {
                        override fun onConsoleMessage(message: android.webkit.ConsoleMessage): Boolean {
                            android.util.Log.d(
                                "WebViewConsole",
                                "${message.message()} (${message.sourceId()}:${message.lineNumber()})",
                            )
                            return true
                        }
                    }
                }.also { webViewRef = it }
            },
            update = { webView ->
                // Чтение reloadTrigger здесь -- единственное, что заставляет
                // Compose повторно вызвать update() при нажатии "Повторить"
                // (см. onRetry выше); без обращения к состоянию внутри
                // update() эта лямбда для AndroidView выполняется один раз.
                @Suppress("UNUSED_EXPRESSION") reloadTrigger
                // ?token= только в первом запросе -- сервер сам закрепляет
                // его в cookie remote_token (см. routes/home.py::
                // set_token_cookie на сервере), дальнейшая навигация внутри
                // WebView (клик по плитке Imagine и т.д.) кук достаточно.
                // openPath (см. EXTRA_OPEN_PATH) -- обычная страница на
                // /apps/imagine/, а не отдельный API -- ей тоже нужен тот
                // же ?token= в первом запросе, ровно как и домашней "/".
                // tokenSeparator -- см. его объявление выше про "&" вместо
                // "?", когда openPath уже несёт свой ?prompt_id=...
                webView.loadUrl("$baseUrl${tokenSeparator}token=${device.accessToken}")
            },
        )
    }
}
