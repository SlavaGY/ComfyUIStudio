package com.comfyuistudio.terminal.discovery

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.util.Log
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow

/**
 * Android-сторона mDNS-обнаружения — §Этап 5/6 дорожной карты
 * (ComfyUIStudio_Remote_Roadmap.md). Тип сервиса и вся семантика
 * должны совпадать 1:1 с серверным `mdns.py` (см. его докстринг:
 * "при реализации Android-стороны нужно использовать РОВНО эту же
 * строку") — сервер объявляет `_comfyuistudio._tcp.local.` через
 * zeroconf; `NsdManager` на Android ожидает тип БЕЗ суффикса
 * `.local.` (сама библиотека дописывает его).
 *
 * Обнаружение — только удобство (см. mdns.py на сервере: "само
 * обнаружение никак не участвует в аутентификации"), поэтому этот
 * сервис ничего не знает про pairing/токены — отдаёт наружу только
 * список (имя, host, port), из которого PairingScreen формирует
 * список "плиток" ПК для выбора, а ручной ввод IP остаётся
 * равноценным запасным вариантом (Remote мог быть не в этой же LAN,
 * mDNS мог не пройти через изолированный Wi-Fi роутер и т.п.).
 */
class DiscoveryService(context: Context) {

    private val nsdManager = context.applicationContext
        .getSystemService(Context.NSD_SERVICE) as NsdManager

    data class DiscoveredServer(
        val name: String,
        val host: String,
        val port: Int,
    )

    /**
     * Активна, пока подписан коллектор Flow — `awaitClose` останавливает
     * `NsdManager.discoverServices`, так что достаточно отменить
     * корутину (например, при уходе с экрана Pairing), чтобы не жечь
     * батарею фоновым мультикастом бесконечно.
     */
    fun discover(): Flow<DiscoveredServer> = callbackFlow {
        val resolveListener = object : NsdManager.ResolveListener {
            override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {
                Log.w(TAG, "Не удалось разрешить ${serviceInfo.serviceName}: код $errorCode")
            }

            override fun onServiceResolved(serviceInfo: NsdServiceInfo) {
                val host = serviceInfo.host?.hostAddress ?: return
                trySend(
                    DiscoveredServer(
                        name = serviceInfo.serviceName,
                        host = host,
                        port = serviceInfo.port,
                    )
                )
            }
        }

        val discoveryListener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(regType: String) {
                Log.i(TAG, "mDNS-поиск запущен ($regType)")
            }

            override fun onServiceFound(serviceInfo: NsdServiceInfo) {
                // resolveService нельзя вызывать параллельно для нескольких
                // сервисов на некоторых версиях платформы — здесь это не
                // проблема (обычно 1-2 ПК Studio в домашней сети), но при
                // желании ужесточить можно сериализовать через очередь.
                try {
                    nsdManager.resolveService(serviceInfo, resolveListener)
                } catch (e: IllegalArgumentException) {
                    // "listener already in use" -- см. комментарий выше;
                    // безопаснее пропустить один результат, чем уронить
                    // весь дискавери-флоу из-за гонки резолвов.
                    Log.w(TAG, "resolveService пропущен: ${e.message}")
                }
            }

            override fun onServiceLost(serviceInfo: NsdServiceInfo) {
                Log.i(TAG, "Сервис пропал из сети: ${serviceInfo.serviceName}")
            }

            override fun onDiscoveryStopped(serviceType: String) {}

            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                Log.e(TAG, "Не удалось запустить поиск: код $errorCode")
                close()
            }

            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {
                Log.e(TAG, "Не удалось остановить поиск: код $errorCode")
            }
        }

        nsdManager.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, discoveryListener)

        awaitClose {
            try {
                nsdManager.stopServiceDiscovery(discoveryListener)
            } catch (e: IllegalArgumentException) {
                // Дискавери уже был остановлен (например, onStartDiscoveryFailed
                // выше уже закрыл канал) -- не критично.
            }
        }
    }

    private companion object {
        const val TAG = "DiscoveryService"
        // БЕЗ ".local." -- см. докстринг класса. Значение должно
        // оставаться синхронизировано с SERVICE_TYPE в
        // comfyui_studio/remote/mdns.py на сервере.
        const val SERVICE_TYPE = "_comfyuistudio._tcp."
    }
}
