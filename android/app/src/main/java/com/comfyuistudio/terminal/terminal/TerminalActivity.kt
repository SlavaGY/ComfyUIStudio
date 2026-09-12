package com.comfyuistudio.terminal.terminal

import android.annotation.SuppressLint
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
import com.comfyuistudio.terminal.tunnel.TunnelConfigStore
import com.comfyuistudio.terminal.tunnel.TunnelSettingsActivity
import com.comfyuistudio.terminal.tunnel.VpnServiceTunnelProvider
import com.comfyuistudio.terminal.tunnel.parseTunnelConfig
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
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

    // §Этап 9 -- туннель поднимается ТОЛЬКО если пользователь явно
    // включил его в TunnelSettingsActivity (см. её же "Использовать вне
    // дома"). Флаг по умолчанию выключен (см. TunnelConfigStore.enabled)
    // -- значит для всех, кто не настраивал VPN, весь этот блок ничего
    // не меняет в уже проверенном поведении: tunnelReady сразу true,
    // TerminalWebView показывается как и раньше, без единого лишнего
    // кадра ожидания или системного диалога.
    val tunnelStore = remember { TunnelConfigStore(context) }
    var tunnelReady by remember { mutableStateOf(!tunnelStore.enabled) }
    var tunnelError by remember { mutableStateOf<String?>(null) }
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
                    tunnelError = "VPN: ${e.message}"
                } finally {
                    tunnelReady = true
                }
            }
        } else {
            tunnelError = "Нет согласия на VPN -- пробуем без туннеля."
            tunnelReady = true
        }
    }

    LaunchedEffect(Unit) {
        if (!tunnelStore.enabled) return@LaunchedEffect
        val raw = tunnelStore.loadRawConfig()
        if (raw == null) {
            // Включено, но конфиг почему-то не сохранён (не должно
            // случаться через обычный UI TunnelSettingsActivity, но не
            // повод блокировать сам терминал, если всё же произошло).
            tunnelReady = true
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
                tunnelReady = true
            }
        } catch (e: Exception) {
            // Не удалось поднять туннель (битый конфиг, сервер не
            // ответил на handshake и т.п.) -- НЕ блокируем терминал
            // насовсем: если пользователь на самом деле дома, обычный
            // LAN-путь ниже (TerminalWebView) отработает и без VPN как
            // прежде, а если нет -- увидит уже привычный
            // ConnectionErrorOverlay из TerminalWebView, просто без
            // помощи VPN в этой попытке.
            tunnelError = "VPN: ${e.message}"
            tunnelReady = true
        }
    }

    // Гасим туннель при уходе с экрана терминала -- см. решение "вариант
    // A" в роадмапе, §Этап 9: "приложение само поднимает/гасит туннель
    // при открытии/закрытии". thread{}+runBlocking, а не
    // rememberCoroutineScope() -- тот отменяется вместе с самим onDispose,
    // гонка могла бы прервать disconnect() на середине.
    DisposableEffect(Unit) {
        onDispose {
            if (tunnelStore.enabled) {
                thread { runBlocking { VpnServiceTunnelProvider.disconnect() } }
            }
        }
    }

    Box(modifier = Modifier.fillMaxSize()) {
        if (!tunnelReady) {
            Column(
                modifier = Modifier.fillMaxSize().padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = androidx.compose.foundation.layout.Arrangement.Center,
            ) {
                CircularProgressIndicator()
                androidx.compose.foundation.layout.Spacer(modifier = Modifier.padding(4.dp))
                Text("Подключение к VPN…")
            }
        } else {
            TerminalWebView(device, openPath)
            tunnelError?.let {
                // Ненавязчивое уведомление поверх WebView -- не
                // блокирует терминал (см. комментарии выше про то, что
                // сбой VPN не должен мешать обычной работе дома).
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

        OutlinedButton(
            onClick = { context.startActivity(Intent(context, TunnelSettingsActivity::class.java)) },
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(12.dp),
        ) {
            Text("VPN")
        }
    }
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun TerminalWebView(device: TokenStore.Device, openPath: String?) {
    val context = androidx.compose.ui.platform.LocalContext.current
    var connectionError by remember { mutableStateOf(false) }
    var reloadTrigger by remember { mutableStateOf(0) }

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
                        onLoadStarted = { loadGeneration++ },
                    )
                }
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
