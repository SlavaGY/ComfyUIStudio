package com.comfyuistudio.terminal.terminal

import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient

/**
 * §Этап 6 дорожной карты называет это "инъекция заголовка через
 * shouldInterceptRequest" (вариант 1 из proxy_auth.py на сервере). По
 * факту реализации выбран **вариант 2 (cookie)** вместо заголовка:
 *
 * - `proxy_auth.resolve_device_and_token` на сервере (см. её докстринг)
 *   и так уже реализует ровно эту схему для ОБЫЧНОГО браузера —
 *   `?token=...` в первом запросе закрепляется как cookie
 *   `remote_token`, дальше сервер принимает её сам, без каких-либо
 *   особых действий клиента на каждый последующий запрос.
 * - `CookieManager.setCookie()` перед первой загрузкой — единственное,
 *   что нужно сделать здесь; `shouldInterceptRequest`, дописывающий
 *   заголовок Authorization к каждому суб-запросу страницы (CSS/JS/
 *   картинки/проксируемые пути Imagine), пришлось бы поддерживать
 *   отдельно и она не покрыла бы WebSocket (`routes/ws.py` на сервере
 *   принимает токен только как query-параметр — браузерный/WebView
 *   WebSocket API не даёт задать заголовки на хендшейк вообще, и это
 *   уже решено на уровне JS-фронтенда Imagine через
 *   `window.__REMOTE_TOKEN__`, см. routes/home.py).
 * - Итог: cookie полностью покрывает и HTML/статику, и обходит
 *   WS-ограничение (JS сам подставляет токен из `window.__REMOTE_TOKEN__`),
 *   т.е. тот же путь, что уже подтверждён живым тестированием с
 *   обычного мобильного браузера (см. README, этап 4) — WebView здесь
 *   просто наследует уже рабочую схему вместо повторной реализации.
 *
 * Второй обязанностью остаётся то, что и планировалось в дорожной
 * карте: обработка "сервер недоступен" — нативный экран reconnect
 * вместо белого экрана ошибки браузера (см. TerminalActivity, которая
 * слушает [onConnectionError]/[onPageLoaded]).
 */
class AuthWebViewClient(
    private val onConnectionError: () -> Unit,
    private val onPageLoaded: () -> Unit,
    private val onLoadStarted: () -> Unit,
) : WebViewClient() {

    override fun onPageStarted(view: WebView, url: String?, favicon: android.graphics.Bitmap?) {
        super.onPageStarted(view, url, favicon)
        // НОВОЕ (живой отчёт: "Imagine уходил в загрузку на 2 минуты" +
        // просьба добавить кнопку "Повторить" на случай "забыл
        // подключиться к общему Wi-Fi/запустить Studio") --
        // `onPageStarted` (в отличие от [onReceivedError] ниже) вызывается
        // документированно ТОЛЬКО для навигаций главного документа --
        // как для самой первой загрузки из TerminalActivity, так и для
        // ЛЮБОЙ последующей (переход по ссылке ВНУТРИ WebView, например
        // с домашней плитки на Imagine) -- про вторые Compose-код в
        // TerminalActivity в принципе не может узнать сам, только через
        // этот колбэк. Используется в TerminalWebView, чтобы перезапускать
        // собственный (короткий) таймаут ожидания загрузки -- см. его
        // докстринг там же про то, почему [onReceivedError] один
        // недостаточен: если телефон просто не в той же сети, что ПК,
        // соединение не завершается быстрой явной ошибкой -- ОС пытается
        // достучаться и только потом, спустя СВОЙ куда более долгий
        // таймаут по умолчанию (те самые интервалы "пара минут" из
        // отчёта), сама вызовет onReceivedError.
        onLoadStarted()
    }

    override fun onReceivedError(
        view: WebView,
        request: WebResourceRequest,
        error: WebResourceError,
    ) {
        super.onReceivedError(view, request, error)
        // Ошибка на главном документе (не на второстепенном суб-ресурсе
        // вроде фавиконки) -- считаем сервер недоступным. isForMainFrame
        // доступен с API 23+, минимальный API этого модуля -- 26.
        if (request.isForMainFrame) {
            onConnectionError()
        }
    }

    override fun onPageFinished(view: WebView, url: String?) {
        super.onPageFinished(view, url)
        onPageLoaded()
    }
}
