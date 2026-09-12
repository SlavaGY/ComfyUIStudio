package com.comfyuistudio.terminal

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import com.comfyuistudio.terminal.pairing.PairingScreen
import com.comfyuistudio.terminal.pairing.TokenStore
import com.comfyuistudio.terminal.terminal.TerminalActivity

/**
 * `MainActivity.kt` — §Этап 6 дорожной карты: "роутинг: если нет
 * сохранённого устройства → Pairing, иначе → Terminal". Проверка
 * — прямо при запуске, до какого-либо UI из этой Activity: если
 * устройство уже сопряжено, MainActivity сама никогда не отрисовывается
 * — сразу передаёт управление TerminalActivity и завершается, чтобы
 * не оставаться в стеке "под" WebView.
 */
class MainActivity : ComponentActivity() {

    // §Этап 6.5 -- POST_NOTIFICATIONS на API 33+ ОБЯЗАН запрашиваться
    // как runtime-разрешение (см. манифест) -- без него FcmService.kt
    // просто не сможет ничего показать. Результат никак не влияет на
    // роутинг ниже: push -- опциональная функция (см. её же докстринг
    // "отсутствие Firebase-проекта не должно ничего ломать" -- то же
    // верно и для отказа в разрешении), отказ не блокирует ни Pairing,
    // ни Terminal.
    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    // §Этап "автосохранение сгенерированных картинок" -- см.
    // GeneratedImageSaver.kt: нужно ТОЛЬКО на API 26-28 (до Scoped
    // Storage, см. манифест про maxSdkVersion="28" у самого
    // разрешения) -- на более новых устройствах permission-лаунчер
    // просто не понадобится (см. requestGalleryPermissionIfNeeded ниже).
    private val galleryPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestNotificationPermissionIfNeeded()
        requestGalleryPermissionIfNeeded()

        val tokenStore = TokenStore(this)
        if (tokenStore.hasDevice()) {
            startActivity(Intent(this, TerminalActivity::class.java))
            finish()
            return
        }

        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    PairingScreen(
                        onPaired = {
                            startActivity(Intent(this, TerminalActivity::class.java))
                            finish()
                        },
                    )
                }
            }
        }
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ContextCompat.checkSelfPermission(
            this, Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
        if (!granted) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    /**
     * См. GeneratedImageSaver.kt и WRITE_EXTERNAL_STORAGE в манифесте
     * (maxSdkVersion="28") -- начиная с API 29 (Scoped Storage) запись
     * собственных файлов приложения в MediaStore разрешения не
     * требует вовсе, поэтому на таких устройствах это разрешение даже
     * не значится в манифесте и просто нечего запрашивать. Как и у
     * уведомлений выше -- отказ ни на что не влияет: сохранение
     * картинок опционально (см. её же докстринг), не блокирует ни
     * Pairing, ни Terminal, ни сам push.
     */
    private fun requestGalleryPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) return
        val granted = ContextCompat.checkSelfPermission(
            this, Manifest.permission.WRITE_EXTERNAL_STORAGE,
        ) == PackageManager.PERMISSION_GRANTED
        if (!granted) {
            galleryPermissionLauncher.launch(Manifest.permission.WRITE_EXTERNAL_STORAGE)
        }
    }
}
