package com.comfyuistudio.terminal.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/**
 * Единая тёмная тема всех нативных экранов (Pairing, терминал, SSH, VPN,
 * экран "Нет связи с ПК"): чёрный фон и синие буквы -- те же цвета, что у
 * res/values/colors.xml и у домашней страницы Remote.
 *
 * Раньше каждая Activity оборачивала экран в голый `MaterialTheme { }`,
 * то есть в СВЕТЛУЮ палитру Material3 по умолчанию -- отсюда белые
 * экраны "Подключение по SSH…", настроек SSH/VPN и т.д. поверх тёмной
 * темы окна.
 */
private val Background = Color(0xFF111111)
private val Surface = Color(0xFF222222)
private val Accent = Color(0xFF4C8DFF)
private val AccentDim = Color(0xFF7FA8F0)   // вторичный текст, подсказки
private val Outline = Color(0xFF2F4F8A)     // рамки полей/кнопок
private val ErrorRed = Color(0xFFE5484D)

private val ComfyDarkColors = darkColorScheme(
    primary = Accent,
    onPrimary = Background,
    primaryContainer = Surface,
    onPrimaryContainer = Accent,
    secondary = Accent,
    onSecondary = Background,
    secondaryContainer = Surface,
    onSecondaryContainer = Accent,
    tertiary = Accent,
    onTertiary = Background,
    background = Background,
    onBackground = Accent,
    surface = Background,
    onSurface = Accent,
    surfaceVariant = Surface,
    onSurfaceVariant = AccentDim,
    surfaceTint = Accent,
    surfaceContainerLowest = Background,
    surfaceContainerLow = Surface,
    surfaceContainer = Surface,
    surfaceContainerHigh = Surface,
    surfaceContainerHighest = Surface,
    outline = Outline,
    outlineVariant = Outline,
    error = ErrorRed,
    onError = Color.White,
)

@Composable
fun ComfyStudioTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = ComfyDarkColors, content = content)
}
