package com.comfyuistudio.terminal.fcm

import com.comfyuistudio.terminal.pairing.RemotePairingClient
import com.comfyuistudio.terminal.pairing.TokenStore
import com.google.firebase.messaging.FirebaseMessaging
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext

/**
 * §Этап 6.5 -- общая точка получения текущего FCM-токена, использовалась
 * раньше только из PairingScreen.kt (запрос токена прямо перед
 * `pair/confirm`). Вынесена сюда (было -- приватная функция в
 * PairingScreen.kt) после живого бага: устройство, сопряжённое ДО того,
 * как в проект добавили `google-services.json`/настроили сервис-аккаунт,
 * никогда не отправляло токен на сервер -- pairing происходит только
 * один раз, `MainActivity` при повторном запуске сразу уходит в
 * `TerminalActivity`, минуя `PairingScreen` целиком (см.
 * `syncFcmTokenIfPaired` ниже -- та же функция получения токена теперь
 * вызывается и оттуда, при каждом открытии терминала, а не только при
 * pairing).
 */

/**
 * Оборачивает callback-API Firebase (`Task<String>`) в suspend-функцию.
 * Возвращает null (а не бросает исключение), если:
 * - Firebase не настроен в этой сборке (нет `google-services.json`,
 *   см. комментарий в app/build.gradle.kts) -- `FirebaseMessaging.
 *   getInstance()` в этом случае может бросить `IllegalStateException`
 *   ("Default FirebaseApp is not initialized") прямо при вызове;
 * - сам запрос токена не удался (нет сети, Google Play Services
 *   недоступны на устройстве и т.п., см. addOnFailureListener).
 * В обоих случаях вызывающая сторона просто не отправляет токен --
 * сервер (`fcm.py`) и так трактует его отсутствие как "push не
 * отправляются", это ожидаемый, а не аварийный случай (см. §Этап 6.5).
 */
suspend fun fetchFcmTokenOrNull(): String? = try {
    suspendCancellableCoroutine { continuation ->
        try {
            FirebaseMessaging.getInstance().token
                .addOnSuccessListener { token ->
                    if (continuation.isActive) continuation.resumeWith(Result.success(token))
                }
                .addOnFailureListener {
                    if (continuation.isActive) continuation.resumeWith(Result.success(null))
                }
        } catch (e: IllegalStateException) {
            // Firebase не инициализирован (нет google-services.json) --
            // см. докстринг функции.
            if (continuation.isActive) continuation.resumeWith(Result.success(null))
        }
    }
} catch (e: Exception) {
    null
}

/**
 * НОВОЕ (живой баг, см. докстринг файла) -- вызывается из
 * TerminalActivity.kt при КАЖДОМ запуске уже сопряжённого устройства,
 * не только из PairingScreen.kt при самом pairing. Идемпотентно и
 * дёшево: если токен на сервере уже актуален, повторная отправка
 * ничего не меняет (см. `update_fcm_token` на сервере -- просто
 * перезаписывает тем же значением); если Firebase был настроен уже
 * ПОСЛЕ того, как устройство сопряжено (ровно наш случай), это
 * единственный способ довезти токен до сервера без повторного pairing.
 * Не бросает исключений и ничего не возвращает вызывающей стороне --
 * это фоновая синхронизация, а не действие, за которым UI должен
 * следить (ни ошибка сети, ни отсутствие Firebase не должны как-либо
 * влиять на открытие терминала).
 */
suspend fun syncFcmTokenIfPaired(device: TokenStore.Device) {
    val token = fetchFcmTokenOrNull() ?: return
    // withContext(Dispatchers.IO) -- ОБЯЗАТЕЛЕН здесь, а не на совести
    // вызывающей стороны: TerminalActivity.kt вызывает эту функцию из
    // LaunchedEffect, который выполняется на главном потоке
    // (AndroidUiDispatcher) -- живой краш (NetworkOnMainThreadException)
    // подтвердил, что RemotePairingClient.updateFcmToken() (синхронный
    // OkHttp-вызов) не может выполняться там напрямую. PairingScreen.kt
    // переключает поток сам на своей стороне (withContext(Dispatchers.IO)
    // вокруг pairingClient.confirm(...)), но полагаться на то, что каждый
    // будущий вызывающий код об этом помнит -- ненадёжно; переключение
    // потока -- обязанность самой функции, делающей сетевой вызов.
    withContext(Dispatchers.IO) {
        RemotePairingClient().updateFcmToken(device.host, device.port, device.accessToken, token)
    }
}
