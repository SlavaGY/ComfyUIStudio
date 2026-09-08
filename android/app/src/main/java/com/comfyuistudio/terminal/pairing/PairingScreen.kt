package com.comfyuistudio.terminal.pairing

import android.os.Build
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.comfyuistudio.terminal.discovery.DiscoveryService
import com.comfyuistudio.terminal.fcm.fetchFcmTokenOrNull
import kotlinx.coroutines.launch

/**
 * Единственный полностью нативный UI-экран терминала — §Этап 6
 * дорожной карты. До появления токена показывать серверную страницу
 * нечем (нет сессии, см. §0.3), поэтому весь flow — discovery →
 * выбор ПК (или ручной ввод адреса) → ввод 6-значного кода →
 * `pair/confirm` → сохранение в TokenStore — на чистом Compose,
 * без WebView.
 *
 * Код (`pair/start`) выдаётся на САМОМ ПК (см. routes/pairing_routes.py:
 * `pair/start` защищён `require_loopback` — с телефона его получить
 * нельзя), пользователь просто переписывает то, что видит в разделе
 * «Удалённый доступ» настроек Studio, сюда.
 */
@Composable
fun PairingScreen(onPaired: () -> Unit) {
    val context = LocalContext.current
    val tokenStore = remember { TokenStore(context) }
    val pairingClient = remember { RemotePairingClient() }
    val discoveryService = remember { DiscoveryService(context) }
    val scope = rememberCoroutineScope()

    val servers = remember { mutableStateListOf<DiscoveryService.DiscoveredServer>() }
    var selectedServer by remember { mutableStateOf<DiscoveryService.DiscoveredServer?>(null) }
    var manualHost by remember { mutableStateOf("") }
    var manualPort by remember { mutableStateOf("7861") }
    var useManualEntry by remember { mutableStateOf(false) }
    var code by remember { mutableStateOf("") }
    var deviceName by remember { mutableStateOf(Build.MODEL ?: "Телефон") }
    var isConnecting by remember { mutableStateOf(false) }
    var errorMessage by remember { mutableStateOf<String?>(null) }

    // Поиск активен, пока открыт этот экран (Flow отменяется вместе с
    // корутиной при выходе из композиции — см. DiscoveryService.discover()).
    LaunchedEffect(Unit) {
        discoveryService.discover().collect { found ->
            if (servers.none { it.host == found.host && it.port == found.port }) {
                servers.add(found)
            }
        }
    }

    val (targetHost, targetPort) = remember(selectedServer, manualHost, manualPort, useManualEntry) {
        if (useManualEntry || selectedServer == null) {
            manualHost to (manualPort.toIntOrNull() ?: 0)
        } else {
            selectedServer!!.host to selectedServer!!.port
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("ComfyUI Studio", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
        Text(
            "Найдите свой ПК в сети или введите адрес вручную, затем введите код сопряжения из настроек Studio («Удалённый доступ»).",
            style = MaterialTheme.typography.bodyMedium,
        )

        if (!useManualEntry) {
            Text("Найденные ПК", style = MaterialTheme.typography.titleSmall)
            if (servers.isEmpty()) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(8.dp))
                    Text("Поиск в локальной сети…", style = MaterialTheme.typography.bodySmall)
                }
            } else {
                LazyColumn(modifier = Modifier.weight(1f, fill = false)) {
                    items(servers) { server ->
                        val selected = selectedServer == server
                        Card(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(vertical = 4.dp),
                            colors = CardDefaults.cardColors(
                                containerColor = if (selected) MaterialTheme.colorScheme.primaryContainer
                                else MaterialTheme.colorScheme.surfaceVariant,
                            ),
                            onClick = { selectedServer = server },
                        ) {
                            Column(Modifier.padding(12.dp)) {
                                Text(server.name, fontWeight = FontWeight.Medium)
                                Text("${server.host}:${server.port}", style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                }
            }
            TextButton(onClick = { useManualEntry = true }) {
                Text("Не вижу свой ПК — ввести адрес вручную")
            }
        } else {
            Text("Адрес ПК", style = MaterialTheme.typography.titleSmall)
            OutlinedTextField(
                value = manualHost,
                onValueChange = { manualHost = it },
                label = { Text("IP-адрес (например, 192.168.1.42)") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = manualPort,
                onValueChange = { manualPort = it.filter(Char::isDigit) },
                label = { Text("Порт") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            TextButton(onClick = { useManualEntry = false }) {
                Text("Вернуться к поиску по сети")
            }
        }

        HorizontalDivider()

        OutlinedTextField(
            value = code,
            onValueChange = { code = it },
            label = { Text("Код сопряжения (напр. 482-731)") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = deviceName,
            onValueChange = { deviceName = it },
            label = { Text("Имя этого устройства") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )

        errorMessage?.let {
            Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
        }

        Button(
            onClick = {
                errorMessage = null
                if (targetHost.isBlank() || targetPort <= 0) {
                    errorMessage = "Укажите адрес ПК."
                    return@Button
                }
                if (code.isBlank()) {
                    errorMessage = "Введите код сопряжения."
                    return@Button
                }
                isConnecting = true
                scope.launch {
                    val result = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                        // §Этап 6.5 -- FCM-токен запрашивается прямо перед
                        // pairing, а не заранее (проще: одно место вместо
                        // "запросить при старте приложения + не забыть
                        // передать сюда же"), см. fetchFcmTokenOrNull()
                        // ниже про то, почему это безопасно даже без
                        // настроенного Firebase.
                        val fcmToken = fetchFcmTokenOrNull()
                        pairingClient.confirm(targetHost, targetPort, code.trim(), deviceName.trim(), fcmToken)
                    }
                    isConnecting = false
                    when (result) {
                        is RemotePairingClient.Result.Success -> {
                            tokenStore.save(
                                TokenStore.Device(
                                    deviceId = result.deviceId,
                                    accessToken = result.accessToken,
                                    host = targetHost,
                                    port = targetPort,
                                    deviceName = deviceName.trim(),
                                )
                            )
                            onPaired()
                        }
                        is RemotePairingClient.Result.Failure -> {
                            errorMessage = result.message
                        }
                    }
                }
            },
            enabled = !isConnecting,
            modifier = Modifier.fillMaxWidth(),
        ) {
            if (isConnecting) {
                CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
            } else {
                Text("Подключиться")
            }
        }
    }
}

