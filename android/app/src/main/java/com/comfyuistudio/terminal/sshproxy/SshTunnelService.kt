package com.comfyuistudio.terminal.sshproxy

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.comfyuistudio.terminal.R
import kotlin.concurrent.thread

/**
 * НАЙДЕННЫЙ БАГ (живой отчёт: "уведомление о завершении генерации не
 * пришло, картинки не скачались" при доступе вне дома через SSH) --
 * до этого класса `SshSocksProxy.connect()`/`disconnect()` вызывались
 * прямо из `TerminalActivity`'s `LaunchedEffect`/`DisposableEffect`
 * (см. её старую версию) -- то есть туннель жил РОВНО пока Compose-
 * экран терминала оставался в композиции. Как только пользователь
 * сворачивал приложение (а именно тогда и должен приходить push о
 * завершении генерации, см. докстринг FcmService.kt про "работает,
 * когда WebView не выполняется"), `onDispose` тут же рвал SSH-сессию --
 * и `GeneratedImageSaver`/`RemotePairingClient` (см. их
 * `proxiedClient()`) оставались вообще без туннеля, через который
 * можно достучаться до LAN-адреса ПК снаружи домашней сети. Самого
 * push-уведомления это не касается (FCM доставляется через сервисы
 * Google, а не через этот туннель) -- если оно тоже не приходит, это
 * отдельная, не связанная с этим классом проблема (см. комментарий у
 * `TerminalScreen` в TerminalActivity.kt про батарейные ограничения).
 *
 * Foreground-сервис с постоянным уведомлением -- стандартный для
 * Android способ сказать системе "этот процесс сейчас реально что-то
 * делает, не убивай его агрессивно при уходе в фон", в отличие от
 * обычного фонового потока, который система вправе прибить в любой
 * момент после сворачивания. Это НЕ гарантия (Android всё ещё может
 * убить процесс при нехватке памяти, см. категорию `dataSync`), но
 * заметно снижает вероятность обрыва просто от самого сворачивания
 * экрана терминала -- именно того случая, который и описан в отчёте.
 *
 * Сам туннель (`SshSocksProxy`, синглтон-объект) не меняется вообще --
 * этот сервис только управляет ТЕМ, из какого места жизненного цикла
 * вызываются его `connect()`/`disconnect()`.
 */
class SshTunnelService : Service() {

    companion object {
        private const val ACTION_START = "com.comfyuistudio.terminal.sshproxy.action.START"
        private const val ACTION_STOP = "com.comfyuistudio.terminal.sshproxy.action.STOP"
        private const val CHANNEL_ID = "ssh_tunnel"
        // Отдельный ID от FcmService.NOTIFICATION_ID (=1) -- иначе одно
        // уведомление перезаписывало бы другое.
        private const val NOTIFICATION_ID = 2

        /** Поднимает туннель (если ещё не поднят) и переводит сервис в
         * foreground -- вызывать из TerminalActivity при открытии
         * терминала с включённым SSH (см. её докстринг). Профиль
         * сервис читает сам из `SshProxyConfigStore`, а не через intent
         * -- тот же профиль нужен и при возможном автоматическом
         * перезапуске системой (`START_STICKY` ниже), где исходный
         * intent уже недоступен. */
        fun start(context: Context) {
            val intent = Intent(context, SshTunnelService::class.java).setAction(ACTION_START)
            ContextCompat.startForegroundService(context, intent)
        }

        /** Гасит туннель и останавливает сервис -- вызывать явно при
         * выключении тумблера "Использовать вне дома" (см.
         * SshProxySettingsActivity.kt) или по кнопке "Отключить" там
         * же. Намеренно НЕ вызывается из TerminalActivity при обычном
         * сворачивании/закрытии экрана терминала -- см. докстринг
         * класса про то, зачем вообще этот сервис существует. */
        fun stop(context: Context) {
            context.startService(Intent(context, SshTunnelService::class.java).setAction(ACTION_STOP))
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            thread { SshSocksProxy.disconnect() }
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }

        ensureChannel()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, buildNotification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIFICATION_ID, buildNotification())
        }

        // Уже подключено (например, TerminalActivity дважды дёрнула
        // start() -- открыл терминал, свернул, снова открыл, пока
        // сервис ещё жив) -- не переподключаемся заново без нужды,
        // SshSocksProxy.connect() и так сам делает disconnect() перед
        // новым подключением, но это лишний обрыв уже рабочего туннеля.
        if (!SshSocksProxy.isRunning()) {
            val store = SshProxyConfigStore(applicationContext)
            val profile = store.loadProfile()
            if (profile == null) {
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }
            thread {
                try {
                    SshSocksProxy.connect(
                        profile.host, profile.port, profile.username,
                        profile.privateKeyPem, profile.passphrase, store,
                    )
                } catch (e: Exception) {
                    // lastError уже сохранён самим SshSocksProxy.connect()
                    // до throw -- TerminalActivity сама проверяет
                    // isRunning()/lastError после запуска сервиса (см. её
                    // докстринг), здесь дополнительно обрабатывать нечем:
                    // у Service нет своего UI, чтобы показать ошибку.
                }
            }
        }
        // START_STICKY -- если систему всё же прикончит процесс при
        // нехватке памяти, она попробует перезапустить сервис (без
        // сохранённого intent, но профиль сервис в любом случае читает
        // сам, см. комментарий выше про START).
        return START_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        // На случай, если сервис уничтожен НЕ через ACTION_STOP (система
        // сама решила освободить память) -- не оставляем висящий
        // ServerSocket/SSH-сессию без владельца.
        thread { SshSocksProxy.disconnect() }
    }

    private fun buildNotification(): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("ComfyUI Studio")
            .setContentText("Доступ вне дома активен (SSH)")
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            "SSH-туннель",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Постоянное уведомление, пока активен доступ вне дома через SSH"
        }
        manager.createNotificationChannel(channel)
    }
}
