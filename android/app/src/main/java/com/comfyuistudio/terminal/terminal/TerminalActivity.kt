package com.comfyuistudio.terminal.terminal

import android.annotation.SuppressLint
import android.os.Bundle
import android.webkit.CookieManager
import android.webkit.WebView
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import com.comfyuistudio.terminal.fcm.syncFcmTokenIfPaired
import com.comfyuistudio.terminal.pairing.TokenStore

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
                        TerminalWebView(device, openPath)
                    }
                }
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

    val baseUrl = "http://${device.host}:${device.port}${openPath ?: "/"}"

    if (connectionError) {
        ConnectionErrorOverlay(
            host = "${device.host}:${device.port}",
            onRetry = {
                connectionError = false
                reloadTrigger++
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
                        onPageLoaded = { connectionError = false },
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
                webView.loadUrl("$baseUrl?token=${device.accessToken}")
            },
        )
    }
}
