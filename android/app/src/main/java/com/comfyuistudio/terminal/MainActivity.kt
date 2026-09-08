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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestNotificationPermissionIfNeeded()

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
}
