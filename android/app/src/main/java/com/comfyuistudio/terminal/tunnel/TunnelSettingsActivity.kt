package com.comfyuistudio.terminal.tunnel

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.wireguard.config.Config
import kotlinx.coroutines.launch
import java.io.BufferedReader
import java.io.InputStreamReader

/**
 * §Этап 9 дорожной карты -- экран импорта WireGuard-конфига и
 * управления туннелем (вариант A). Экран целиком локальный: сам сервер
 * WireGuard (Windows-ПК/роутер) настраивается пользователем СНАРУЖИ
 * этого приложения -- сюда только импортируется уже готовый .conf,
 * который стандартно выдаёт `wg genconfig`/панель управления сервера
 * для конкретного клиента (peer).
 *
 * Доступен из MainActivity -- см. её кнопку "Доступ вне дома (VPN)".
 */
class TunnelSettingsActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    TunnelSettingsScreen()
                }
            }
        }
    }
}

@Composable
private fun TunnelSettingsScreen() {
    val context = LocalContext.current
    val store = remember { TunnelConfigStore(context) }
    val scope = rememberCoroutineScope()

    var rawConfig by remember { mutableStateOf(store.loadRawConfig()) }
    var parsedSummary by remember { mutableStateOf<String?>(null) }
    var parseError by remember { mutableStateOf<String?>(null) }
    var enabled by remember { mutableStateOf(store.enabled) }
    var statusText by remember { mutableStateOf(if (VpnServiceTunnelProvider.isConnected()) "Подключено" else "Отключено") }
    var isBusy by remember { mutableStateOf(false) }
    var pendingConfig by remember { mutableStateOf<Config?>(null) }

    // Пересчитываем сводку (см. summarize() ниже) при каждом новом
    // импортированном тексте -- в т.ч. сразу при первом входе на экран,
    // если конфиг уже был сохранён раньше.
    LaunchedEffect(rawConfig) {
        val text = rawConfig
        parseError = null
        parsedSummary = null
        if (text != null) {
            try {
                parsedSummary = summarize(parseTunnelConfig(text))
            } catch (e: Exception) {
                parseError = "Не удалось разобрать конфиг: ${e.message}"
            }
        }
    }

    // Согласие на VPN (VpnService.prepare) -- см. докстринг
    // TunnelProvider.prepareIntent в TunnelManager.kt про то, почему
    // этот шаг не спрятан внутри connect() и должен идти именно отсюда.
    val vpnPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        val config = pendingConfig
        pendingConfig = null
        if (result.resultCode == android.app.Activity.RESULT_OK && config != null) {
            scope.launch {
                isBusy = true
                statusText = "Подключение…"
                try {
                    VpnServiceTunnelProvider.connect(context, config)
                    statusText = "Подключено"
                } catch (e: Exception) {
                    statusText = "Ошибка: ${e.message}"
                }
                isBusy = false
            }
        } else {
            statusText = "Отключено (нет согласия на VPN)"
        }
    }

    fun startTunnel(config: Config) {
        val intent = VpnServiceTunnelProvider.prepareIntent(context)
        if (intent != null) {
            pendingConfig = config
            vpnPermissionLauncher.launch(intent)
        } else {
            scope.launch {
                isBusy = true
                statusText = "Подключение…"
                try {
                    VpnServiceTunnelProvider.connect(context, config)
                    statusText = "Подключено"
                } catch (e: Exception) {
                    statusText = "Ошибка: ${e.message}"
                }
                isBusy = false
            }
        }
    }

    // .conf -- обычный текстовый файл без строго закреплённого MIME-типа
    // на всех устройствах ("*/*" -- принимаем что угодно; читаем как
    // текст вне зависимости от того, что покажет пикер).
    val filePickerLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.GetContent(),
    ) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        try {
            val text = context.contentResolver.openInputStream(uri)?.use { stream ->
                BufferedReader(InputStreamReader(stream)).readText()
            }
            if (text.isNullOrBlank()) {
                parseError = "Файл пуст или не удалось прочитать."
                return@rememberLauncherForActivityResult
            }
            // Валидация СРАЗУ при импорте, а не только при попытке
            // подключиться -- чтобы явная ошибка в самом тексте конфига
            // не всплывала внезапно позже, в момент, когда пользователь
            // уже далеко от дома и меньше всего этого ждёт.
            parseTunnelConfig(text)
            store.saveRawConfig(text)
            rawConfig = text
            parseError = null
        } catch (e: Exception) {
            parseError = "Не удалось разобрать конфиг: ${e.message}"
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("Доступ вне дома", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
        Text(
            "WireGuard-туннель только для этого приложения — остальной трафик телефона " +
                "не затрагивается. Сам WireGuard-сервер (на ПК или роутере) настраивается " +
                "отдельно, здесь только импортируется готовый .conf для этого устройства.",
            style = MaterialTheme.typography.bodyMedium,
        )

        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Важно при выпуске конфига", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Medium)
                Text(
                    "Чтобы туннель поднимался ТОЛЬКО для ComfyUI Studio (а не для всего " +
                        "телефона), добавьте в секцию [Interface] строку:",
                    style = MaterialTheme.typography.bodySmall,
                )
                Text(
                    "IncludedApplications = ${context.packageName}",
                    style = MaterialTheme.typography.bodySmall,
                    fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
                )
                Text(
                    "Также в [Interface] нужен адрес самого ПК В ЭТОЙ сети (напр. " +
                        "Address = 10.13.13.1/32 на клиенте и AllowedIPs у пира — подсеть " +
                        "домашней LAN, а не только адрес сервера, иначе Remote API останется " +
                        "недостижим по своему обычному LAN-адресу через туннель).",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }

        HorizontalDivider()

        Button(onClick = { filePickerLauncher.launch("*/*") }, modifier = Modifier.fillMaxWidth()) {
            Text(if (rawConfig == null) "Импортировать .conf файл" else "Заменить конфиг")
        }

        parseError?.let {
            Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
        }
        parsedSummary?.let {
            Text(it, style = MaterialTheme.typography.bodySmall)
        }

        if (rawConfig != null) {
            HorizontalDivider()

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Column {
                    Text("Использовать вне дома", fontWeight = FontWeight.Medium)
                    Text(
                        "Приложение само поднимет туннель при открытии терминала и погасит " +
                            "при закрытии.",
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                Switch(
                    checked = enabled,
                    onCheckedChange = {
                        enabled = it
                        store.enabled = it
                    },
                )
            }

            HorizontalDivider()

            Text("Проверка соединения", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Medium)
            Text("Статус: $statusText", style = MaterialTheme.typography.bodyMedium)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = {
                        val text = rawConfig ?: return@Button
                        try {
                            startTunnel(parseTunnelConfig(text))
                        } catch (e: Exception) {
                            statusText = "Ошибка: ${e.message}"
                        }
                    },
                    enabled = !isBusy,
                ) {
                    if (isBusy) {
                        CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                    } else {
                        Text("Подключиться сейчас")
                    }
                }
                OutlinedButton(
                    onClick = {
                        scope.launch {
                            isBusy = true
                            VpnServiceTunnelProvider.disconnect()
                            statusText = "Отключено"
                            isBusy = false
                        }
                    },
                    enabled = !isBusy,
                ) {
                    Text("Отключить")
                }
            }

            HorizontalDivider()

            Text(
                "Ограничение: автосохранение картинок из push-уведомлений (см. соответствующий " +
                    "раздел роадмапа) работает, только пока приложение открыто и туннель поднят — " +
                    "туннель не остаётся включённым в фоне, когда приложение полностью закрыто, " +
                    "поэтому push, пришедший вдали от дома при закрытом приложении, само " +
                    "уведомление покажет, но картинки до открытия терминала не скачает.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            TextButton(onClick = {
                store.clearConfig()
                rawConfig = null
                enabled = false
                parsedSummary = null
            }) {
                Text("Удалить сохранённый конфиг")
            }
        }
    }
}

/** Короткая сводка без приватного ключа -- показываем пользователю
 * подтверждение, что файл разобрался правильно, не выводя секрет на
 * экран без необходимости (сам конфиг и так уже хранится зашифрованным,
 * см. TunnelConfigStore -- это просто гигиена отображения, а не
 * дополнительный слой защиты). */
private fun summarize(config: Config): String {
    val addresses = config.getInterface().addresses.joinToString(", ")
    val peer = config.getPeers().firstOrNull()
    val endpoint = peer?.getEndpoint()?.orElse(null)?.toString() ?: "?"
    return "Конфиг разобран: адрес $addresses, сервер $endpoint, пиров: ${config.getPeers().size}"
}
