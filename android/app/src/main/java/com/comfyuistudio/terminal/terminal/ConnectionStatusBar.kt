package com.comfyuistudio.terminal.terminal

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/**
 * "Тонкая нативная рамка вокруг WebView" — §Этап 6 дорожной карты.
 * Два состояния:
 *
 * - [ConnectionErrorOverlay] — сервер не отвечает (см.
 *   AuthWebViewClient.onReceivedError на главном документе):
 *   полноэкранная нативная замена белому экрану ошибки браузера,
 *   с кнопками "Повторить" и "Сменить ПК" (сброс TokenStore обратно к
 *   PairingScreen — §0.3, "кнопка смены ПК, если сопряжено несколько").
 * - Обычная работа WebView не рисует ничего лишнего поверх — терминал
 *   должен выглядеть как страница, отрисованная сервером, а не как
 *   Android-приложение с рамкой вокруг чужого контента.
 */
@Composable
fun ConnectionErrorOverlay(
    host: String,
    onRetry: () -> Unit,
    onChangeServer: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("Нет связи с ПК", style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(8.dp))
        Text(
            "Не удалось подключиться к $host. Убедитесь, что Studio запущена, " +
                "«Удалённый доступ» включён и телефон в той же сети.",
            style = MaterialTheme.typography.bodyMedium,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        )
        Spacer(Modifier.height(24.dp))
        Button(onClick = onRetry) { Text("Повторить") }
        Spacer(Modifier.height(8.dp))
        TextButton(onClick = onChangeServer) { Text("Сменить ПК") }
    }
}
