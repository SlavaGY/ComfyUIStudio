package com.comfyuistudio.terminal.sshproxy

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContract
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.BufferedReader
import java.io.InputStreamReader

/**
 * §Этап 9 дорожной карты, вариант B -- экран настройки SSH-прокси (см.
 * докстринг `SshSocksProxy.kt` про архитектуру целиком). Сервер (обычный
 * OpenSSH на Windows) настраивается пользователем СНАРУЖИ этого
 * приложения -- см. `SSH_Proxy_Server_Setup.md`; сюда только вводятся
 * итоговые параметры подключения и импортируется приватный ключ.
 *
 * Независимый экран от `TunnelSettingsActivity` (вариант A) -- см.
 * докстринг `SshProxyConfigStore` про то, что оба механизма могут быть
 * настроены одновременно, но включён на практике должен быть только
 * один.
 */
class SshProxySettingsActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    SshProxySettingsScreen()
                }
            }
        }
    }
}

/**
 * НАЙДЕННЫЙ БАГ (живой отчёт пользователя, §Этап 9): обычный
 * `ActivityResultContracts.GetContent()` (`ACTION_GET_CONTENT`) на
 * практике открывает не общий выбор приложения, а тот файловый
 * менеджер, что Android уже считает обработчиком по умолчанию для
 * этого действия -- у пользователя это был встроенный "Файлы", который
 * не показывает файлы без расширения (приватный ключ обычно без
 * расширения) и заставлял его переименовывать ключ в *.txt только
 * чтобы файл вообще стал видимым. При этом "Files от Google" тот же
 * самый файл видит нормально.
 *
 * `Intent.createChooser()` -- единственный надёжный способ ВСЕГДА
 * показать диалог выбора приложения (он специально не даёт назначить
 * постоянный обработчик по умолчанию), поэтому пользователь каждый раз
 * может выбрать именно Google Files, а не то, что Android выбрал бы
 * сам. `ACTION_OPEN_DOCUMENT` вместо `ACTION_GET_CONTENT` -- современный
 * SAF-эквивалент с тем же результатом (content:// Uri с временным
 * grant на чтение), обёрнутый в чужой чузер он ведёт себя так же
 * предсказуемо.
 */
private class OpenDocumentWithChooser : ActivityResultContract<String, Uri?>() {
    override fun createIntent(context: Context, input: String): Intent {
        val target = Intent(Intent.ACTION_OPEN_DOCUMENT)
            .addCategory(Intent.CATEGORY_OPENABLE)
            .setType(input)
        return Intent.createChooser(target, "Выберите приложение для импорта ключа")
    }

    override fun parseResult(resultCode: Int, intent: Intent?): Uri? {
        if (resultCode != Activity.RESULT_OK) return null
        return intent?.data
    }
}

@Composable
private fun SshProxySettingsScreen() {
    val context = LocalContext.current
    val store = remember { SshProxyConfigStore(context) }
    val scope = rememberCoroutineScope()

    val existing = remember { store.loadProfile() }
    var host by remember { mutableStateOf(existing?.host ?: "") }
    var port by remember { mutableStateOf((existing?.port ?: 2222).toString()) }
    var username by remember { mutableStateOf(existing?.username ?: "") }
    var privateKeyPem by remember { mutableStateOf(existing?.privateKeyPem ?: "") }
    var passphrase by remember { mutableStateOf(existing?.passphrase ?: "") }
    var enabled by remember { mutableStateOf(store.enabled) }

    var statusText by remember { mutableStateOf(if (SshSocksProxy.isRunning()) "Подключено" else "Отключено") }
    var isBusy by remember { mutableStateOf(false) }

    val filePickerLauncher = rememberLauncherForActivityResult(
        OpenDocumentWithChooser(),
    ) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        try {
            val text = context.contentResolver.openInputStream(uri)?.use { stream ->
                BufferedReader(InputStreamReader(stream)).readText()
            }
            if (!text.isNullOrBlank()) privateKeyPem = text
        } catch (e: Exception) {
            statusText = "Не удалось прочитать файл: ${e.message}"
        }
    }

    fun currentProfileOrNull(): SshProxyConfigStore.Profile? {
        val portInt = port.toIntOrNull() ?: return null
        if (host.isBlank() || username.isBlank() || privateKeyPem.isBlank()) return null
        return SshProxyConfigStore.Profile(
            host = host.trim(),
            port = portInt,
            username = username.trim(),
            privateKeyPem = privateKeyPem,
            passphrase = passphrase.ifBlank { null },
        )
    }

    fun saveAndTest() {
        val profile = currentProfileOrNull()
        if (profile == null) {
            statusText = "Заполните адрес, порт, пользователя и приватный ключ"
            return
        }
        store.saveProfile(profile)
        scope.launch {
            isBusy = true
            statusText = "Подключение…"
            try {
                withContext(Dispatchers.IO) {
                    SshSocksProxy.connect(
                        profile.host, profile.port, profile.username,
                        profile.privateKeyPem, profile.passphrase, store,
                    )
                }
                statusText = "Подключено"
            } catch (e: Exception) {
                statusText = "Ошибка: ${e.message}"
            }
            isBusy = false
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("Доступ вне дома (SSH)", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
        Text(
            "Второй, независимый от VPN способ достучаться до Remote вне домашней сети — " +
                "через обычный SSH-туннель. В отличие от VPN-варианта, не поднимает системный " +
                "VpnService и поэтому не конфликтует с другим VPN на телефоне (Hide.me и т.п.).",
            style = MaterialTheme.typography.bodyMedium,
        )

        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Перед настройкой", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Medium)
                Text(
                    "На ПК должен быть настроен OpenSSH-сервер с отдельной учётной записью " +
                        "только для проброса портов (см. SSH_Proxy_Server_Setup.md). Ниже — " +
                        "параметры ИМЕННО этой учётной записи, не вашего обычного пользователя " +
                        "Windows.",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }

        OutlinedTextField(
            value = host,
            onValueChange = { host = it },
            label = { Text("Адрес сервера (IP или [IPv6])") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )
        OutlinedTextField(
            value = port,
            onValueChange = { port = it },
            label = { Text("Порт SSH") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )
        OutlinedTextField(
            value = username,
            onValueChange = { username = it },
            label = { Text("Пользователь") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )
        OutlinedTextField(
            value = passphrase,
            onValueChange = { passphrase = it },
            label = { Text("Пароль ключа (если есть)") },
            visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )

        Button(onClick = { filePickerLauncher.launch("*/*") }, modifier = Modifier.fillMaxWidth()) {
            Text(if (privateKeyPem.isBlank()) "Импортировать приватный ключ" else "Заменить приватный ключ")
        }
        if (privateKeyPem.isNotBlank()) {
            Text(
                "Ключ загружен (${privateKeyPem.length} символов) — сам текст не показывается.",
                style = MaterialTheme.typography.bodySmall,
            )
        }

        HorizontalDivider()

        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Column {
                Text("Использовать вне дома", fontWeight = FontWeight.Medium)
                Text(
                    "Приложение само поднимет прокси при открытии терминала и погасит при закрытии.",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            Switch(
                checked = enabled,
                onCheckedChange = { checked ->
                    enabled = checked
                    store.enabled = checked
                    // НОВОЕ -- см. докстринг SshTunnelService.kt: выключение
                    // тумблера должно реально гасить фоновый туннель и его
                    // уведомление, а не просто переставать поднимать его в
                    // будущем -- иначе он продолжал бы висеть в фоне до
                    // следующего перезапуска процесса.
                    if (!checked) SshTunnelService.stop(context)
                },
            )
        }

        HorizontalDivider()

        Text("Проверка соединения", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Medium)
        Text("Статус: $statusText", style = MaterialTheme.typography.bodyMedium)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { saveAndTest() }, enabled = !isBusy) {
                if (isBusy) {
                    CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                } else {
                    Text("Сохранить и подключиться")
                }
            }
            OutlinedButton(
                onClick = {
                    scope.launch {
                        isBusy = true
                        // НОВОЕ -- останавливаем именно через сервис (он
                        // сам вызовет SshSocksProxy.disconnect(), см.
                        // SshTunnelService.kt), иначе при активном
                        // тумблере "Использовать вне дома" сервис остался
                        // бы жить и просто заново поднял бы туннель, как
                        // только пользователь снова откроет терминал.
                        withContext(Dispatchers.IO) { SshTunnelService.stop(context) }
                        statusText = "Отключено"
                        isBusy = false
                    }
                },
                enabled = !isBusy,
            ) {
                Text("Отключить")
            }
        }

        Text(
            "Проверка ключа сервера — при первом подключении принимается автоматически и " +
                "запоминается; если сервер потом пришлёт другой ключ (переустановка, подмена) — " +
                "подключение будет отклонено, а не принято молча.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            "Ограничение: как и у VPN-варианта, автосохранение картинок из push работает, только " +
                "пока приложение открыто и прокси поднят.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        TextButton(onClick = {
            store.clearConfig()
            host = ""; port = "2222"; username = ""; privateKeyPem = ""; passphrase = ""; enabled = false
        }) {
            Text("Удалить сохранённый профиль")
        }
    }
}
