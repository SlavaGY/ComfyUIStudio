"""
theme.py
Система тем оформления для редактора конфигов — палитры повторяют темы
из присланного референса (PromptVault): Dark, Light, Nord, Catppuccin Mocha,
Dracula, GitHub Dark. Единая точка правды по цветам и стилям ttk/tk.

Классические tk-виджеты (Listbox, Text, Canvas, Menu) не перекрашиваются
сами при смене темы — конкретные виджеты, которые их используют, должны
реализовать метод apply_theme_colors() (см. widgets.py/characters_tab.py/
promptbuilder_tab.py), либо (для целых вкладок) просто пересоздаются в
main.py при переключении темы.
"""
from __future__ import annotations

import json
import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

FONT_FAMILY      = "Segoe UI"
FONT_NORMAL      = (FONT_FAMILY, 10)
FONT_BOLD        = (FONT_FAMILY, 10, "bold")
FONT_HEADING     = (FONT_FAMILY, 12, "bold")
FONT_MONO        = ("Consolas", 10)
FONT_SMALL       = (FONT_FAMILY, 9)

DANGER_BG    = "#5a1d1d"
DANGER_HOVER = "#7a2727"

# --------------------------------------------------------------------------
# Палитры. Ключи и общая структура одинаковы для каждой темы, значения взяты
# из соответствующих .qss тем присланного приложения (PromptVault).
# --------------------------------------------------------------------------
PALETTES: dict[str, dict[str, str]] = {
    "Dark": {
        "BG": "#1e1e1e", "BG_PANEL": "#252526", "BG_ENTRY": "#2d2d2d", "BG_HOVER": "#3c3c3c",
        "BORDER": "#3c3c3c", "FG": "#e0e0e0", "FG_MUTED": "#9da5b4", "FG_HEADING": "#ffffff",
        "ACCENT": "#4fc3f7", "ACCENT_DARK": "#29b6f6", "ACCENT2": "#4ec9b0",
        "WARN": "#dcdcaa", "ERROR": "#f14c4c",
        "SELECT_BG": "#094771", "SELECT_FG": "#ffffff",
        "TITLEBAR_BG": "#123246", "TITLEBAR_FG": "#ffffff", "TITLEBAR_BORDER": "#0a2436",
    },
    "Light": {
        "BG": "#f5f5f5", "BG_PANEL": "#ffffff", "BG_ENTRY": "#ffffff", "BG_HOVER": "#d0d0d0",
        "BORDER": "#d0d0d0", "FG": "#1e1e1e", "FG_MUTED": "#6b7280", "FG_HEADING": "#0d1117",
        "ACCENT": "#1976d2", "ACCENT_DARK": "#125aa0", "ACCENT2": "#2e7d32",
        "WARN": "#8a6d1a", "ERROR": "#c62828",
        "SELECT_BG": "#cfe4ff", "SELECT_FG": "#0d1117",
        "TITLEBAR_BG": "#1976d2", "TITLEBAR_FG": "#ffffff", "TITLEBAR_BORDER": "#125aa0",
    },
    "Nord": {
        "BG": "#2E3440", "BG_PANEL": "#3B4252", "BG_ENTRY": "#3B4252", "BG_HOVER": "#4C566A",
        "BORDER": "#4C566A", "FG": "#ECEFF4", "FG_MUTED": "#D8DEE9", "FG_HEADING": "#ECEFF4",
        "ACCENT": "#88C0D0", "ACCENT_DARK": "#5E81AC", "ACCENT2": "#A3BE8C",
        "WARN": "#EBCB8B", "ERROR": "#BF616A",
        "SELECT_BG": "#5E81AC", "SELECT_FG": "#ECEFF4",
        "TITLEBAR_BG": "#242933", "TITLEBAR_FG": "#ECEFF4", "TITLEBAR_BORDER": "#4C566A",
    },
    "Catppuccin Mocha": {
        "BG": "#1e1e2e", "BG_PANEL": "#181825", "BG_ENTRY": "#313244", "BG_HOVER": "#45475a",
        "BORDER": "#45475a", "FG": "#cdd6f4", "FG_MUTED": "#a6adc8", "FG_HEADING": "#cdd6f4",
        "ACCENT": "#89b4fa", "ACCENT_DARK": "#6c93d6", "ACCENT2": "#94e2d5",
        "WARN": "#f9e2af", "ERROR": "#f38ba8",
        "SELECT_BG": "#45475a", "SELECT_FG": "#cdd6f4",
        "TITLEBAR_BG": "#11111b", "TITLEBAR_FG": "#cdd6f4", "TITLEBAR_BORDER": "#313244",
    },
    "Dracula": {
        "BG": "#282a36", "BG_PANEL": "#21222c", "BG_ENTRY": "#343746", "BG_HOVER": "#44475a",
        "BORDER": "#44475a", "FG": "#f8f8f2", "FG_MUTED": "#6272a4", "FG_HEADING": "#f8f8f2",
        "ACCENT": "#bd93f9", "ACCENT_DARK": "#9d7bd8", "ACCENT2": "#50fa7b",
        "WARN": "#f1fa8c", "ERROR": "#ff5555",
        "SELECT_BG": "#44475a", "SELECT_FG": "#f8f8f2",
        "TITLEBAR_BG": "#191a21", "TITLEBAR_FG": "#f8f8f2", "TITLEBAR_BORDER": "#44475a",
    },
    "GitHub Dark": {
        "BG": "#0d1117", "BG_PANEL": "#161b22", "BG_ENTRY": "#0d1117", "BG_HOVER": "#30363d",
        "BORDER": "#30363d", "FG": "#c9d1d9", "FG_MUTED": "#8b949e", "FG_HEADING": "#ffffff",
        "ACCENT": "#58a6ff", "ACCENT_DARK": "#1f6feb", "ACCENT2": "#3fb950",
        "WARN": "#d29922", "ERROR": "#f85149",
        "SELECT_BG": "#1f6feb", "SELECT_FG": "#ffffff",
        "TITLEBAR_BG": "#010409", "TITLEBAR_FG": "#c9d1d9", "TITLEBAR_BORDER": "#30363d",
    },
}

DEFAULT_THEME = "Dark"
_current_theme_name = DEFAULT_THEME

# Значения текущей палитры — читаются другими модулями как theme.BG,
# theme.BG_ENTRY и т.д. Обновляются при каждом apply_theme(root, name).
BG = BG_PANEL = BG_ENTRY = BG_HOVER = BORDER = ""
FG = FG_MUTED = FG_HEADING = ""
ACCENT = ACCENT_DARK = ACCENT2 = ""
WARN = ERROR = ""
SELECT_BG = SELECT_FG = ""
TITLEBAR_BG = TITLEBAR_FG = TITLEBAR_BORDER = ""


def available_themes() -> list[str]:
    return list(PALETTES.keys())


def current_theme_name() -> str:
    return _current_theme_name


def _apply_palette_globals(palette: dict[str, str]) -> None:
    g = globals()
    for key, value in palette.items():
        g[key] = value


# --------------------------------------------------------------------------
# Сохранение выбранной темы между запусками (аналог QSettings у PromptVault).
# --------------------------------------------------------------------------
_SETTINGS_PATH = Path.home() / ".prompt_config_editor_settings.json"


def load_saved_theme_name() -> str:
    try:
        data = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
        name = data.get("theme")
        if name in PALETTES:
            return name
    except (OSError, ValueError):
        pass
    return DEFAULT_THEME


def save_theme_name(name: str) -> None:
    try:
        _SETTINGS_PATH.write_text(json.dumps({"theme": name}, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def apply_theme(root: tk.Tk, theme_name: str | None = None) -> ttk.Style:
    """Настраивает тему для всего приложения: обновляет глобальные цвета
    этого модуля, глобальные tk option_add и стиль ttk. Можно вызывать
    повторно (при смене темы) — существующие ttk-виджеты перекрасятся
    сами; классические tk-виджеты (Listbox/Text/Canvas/Menu) — нет, их
    нужно пересоздать или перекрасить вручную (см. apply_theme_colors())."""
    global _current_theme_name
    if theme_name is None:
        theme_name = _current_theme_name
    if theme_name not in PALETTES:
        theme_name = DEFAULT_THEME
    _current_theme_name = theme_name
    _apply_palette_globals(PALETTES[theme_name])

    root.configure(bg=BG)

    # Глобальные дефолты для классических tk-виджетов (Entry, Text, Listbox, Menu...)
    root.option_add("*Background", BG)
    root.option_add("*Foreground", FG)
    root.option_add("*Font", FONT_NORMAL)
    root.option_add("*Entry.Background", BG_ENTRY)
    root.option_add("*Entry.Foreground", FG)
    root.option_add("*Entry.insertBackground", FG)
    root.option_add("*Text.Background", BG_ENTRY)
    root.option_add("*Text.Foreground", FG)
    root.option_add("*Text.insertBackground", FG)
    root.option_add("*Text.selectBackground", SELECT_BG)
    root.option_add("*Text.selectForeground", SELECT_FG)
    root.option_add("*Listbox.Background", BG_ENTRY)
    root.option_add("*Listbox.Foreground", FG)
    root.option_add("*Listbox.selectBackground", SELECT_BG)
    root.option_add("*Listbox.selectForeground", SELECT_FG)
    root.option_add("*Menu.Background", BG_PANEL)
    root.option_add("*Menu.Foreground", FG)
    root.option_add("*Menu.activeBackground", SELECT_BG)
    root.option_add("*Menu.activeForeground", SELECT_FG)
    root.option_add("*Menu.borderWidth", 0)
    root.option_add("*Toplevel.Background", BG)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=FG, font=FONT_NORMAL,
                     bordercolor=BORDER, darkcolor=BG, lightcolor=BG,
                     focuscolor=ACCENT)

    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=BG_PANEL)

    style.configure("TLabel", background=BG, foreground=FG, font=FONT_NORMAL)
    style.configure("Heading.TLabel", background=BG, foreground=FG_HEADING, font=FONT_HEADING)
    style.configure("Muted.TLabel", background=BG, foreground=FG_MUTED, font=FONT_SMALL)
    style.configure("Warn.TLabel", background=BG, foreground=WARN, font=FONT_SMALL)
    style.configure("Error.TLabel", background=BG, foreground=ERROR, font=FONT_SMALL)
    style.configure("Panel.TLabel", background=BG_PANEL, foreground=FG)

    style.configure("TButton", background=BG_PANEL, foreground=FG,
                     bordercolor=BORDER, focusthickness=1, padding=(10, 6))
    style.map("TButton",
              background=[("active", BG_HOVER), ("pressed", BG_HOVER)],
              foreground=[("disabled", FG_MUTED)])

    style.configure("Accent.TButton", background=ACCENT_DARK, foreground="#ffffff", padding=(10, 6))
    style.map("Accent.TButton", background=[("active", ACCENT), ("pressed", ACCENT)])

    style.configure("Danger.TButton", background=DANGER_BG, foreground="#ffffff", padding=(10, 6))
    style.map("Danger.TButton", background=[("active", DANGER_HOVER)])

    style.configure("TEntry", fieldbackground=BG_ENTRY, foreground=FG,
                     bordercolor=BORDER, insertcolor=FG, padding=4)
    style.map("TEntry", fieldbackground=[("disabled", BG_PANEL)])

    style.configure("TCombobox", fieldbackground=BG_ENTRY, background=BG_ENTRY,
                     foreground=FG, arrowcolor=FG, bordercolor=BORDER, padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", BG_ENTRY)],
              foreground=[("disabled", FG_MUTED)])
    root.option_add("*TCombobox*Listbox.background", BG_ENTRY)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", SELECT_BG)
    root.option_add("*TCombobox*Listbox.selectForeground", SELECT_FG)

    style.configure("TSpinbox", fieldbackground=BG_ENTRY, foreground=FG,
                     arrowcolor=FG, bordercolor=BORDER, padding=4)

    style.configure("TCheckbutton", background=BG, foreground=FG)
    style.map("TCheckbutton", background=[("active", BG)])
    style.configure("Panel.TCheckbutton", background=BG_PANEL, foreground=FG)
    style.map("Panel.TCheckbutton", background=[("active", BG_PANEL)])

    style.configure("TNotebook", background=BG, bordercolor=BORDER, tabmargins=(2, 4, 2, 0))
    style.configure("TNotebook.Tab", background=BG_PANEL, foreground=FG_MUTED,
                     padding=(14, 7), font=FONT_NORMAL)
    style.map("TNotebook.Tab",
              background=[("selected", BG)],
              foreground=[("selected", FG_HEADING)])

    style.configure("Treeview", background=BG_ENTRY, fieldbackground=BG_ENTRY,
                     foreground=FG, bordercolor=BORDER, rowheight=26, borderwidth=0)
    style.configure("Treeview.Heading", background=BG_PANEL, foreground=FG_HEADING,
                     font=FONT_BOLD, relief="flat")
    style.map("Treeview.Heading", background=[("active", BG_HOVER)])
    style.map("Treeview",
              background=[("selected", SELECT_BG)],
              foreground=[("selected", SELECT_FG)])

    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", background=BG_PANEL)

    style.configure("TSeparator", background=BORDER)

    style.configure("TScrollbar", background=BG_PANEL, troughcolor=BG,
                     bordercolor=BG, arrowcolor=FG)
    style.map("TScrollbar", background=[("active", BG_HOVER)])

    style.configure("TLabelframe", background=BG, bordercolor=BORDER)
    style.configure("TLabelframe.Label", background=BG, foreground=FG_MUTED, font=FONT_BOLD)

    style.configure("Status.TLabel", background=BG_PANEL, foreground=FG_MUTED,
                     font=FONT_SMALL, padding=(8, 4))

    return style


def _hex_to_colorref(hex_color: str) -> int:
    """'#RRGGBB' -> int в формате Windows COLORREF (0x00BBGGRR), который ждёт DWM."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def apply_windows_titlebar(root: tk.Tk,
                            caption_color: str | None = None,
                            text_color: str | None = None,
                            border_color: str | None = None) -> bool:
    """Красит системный заголовок окна цветом текущей темы, используя
    DWM-атрибуты Windows 11 (build 22000+). На Linux/macOS и на более
    старых Windows просто ничего не делает и возвращает False —
    приложение остаётся полностью рабочим, только заголовок будет
    системного цвета. Иконку (слева от заголовка) эта функция не
    трогает — она берётся из iconphoto/iconbitmap, см. set_app_icon()."""
    caption_color = caption_color or TITLEBAR_BG
    text_color = text_color or TITLEBAR_FG
    border_color = border_color or TITLEBAR_BORDER

    if sys.platform != "win32":
        return False
    try:
        import ctypes

        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        if not hwnd:
            hwnd = root.winfo_id()

        dwmapi = ctypes.windll.dwmapi
        DWMWA_BORDER_COLOR = 34
        DWMWA_CAPTION_COLOR = 35
        DWMWA_TEXT_COLOR = 36

        for attr, color in (
            (DWMWA_CAPTION_COLOR, caption_color),
            (DWMWA_TEXT_COLOR, text_color),
            (DWMWA_BORDER_COLOR, border_color),
        ):
            value = ctypes.c_int(_hex_to_colorref(color))
            dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
        return True
    except Exception:
        # Старая версия Windows / DWM недоступен — просто оставляем системный заголовок.
        return False


def set_app_icon(root: tk.Tk, assets_dir: str) -> None:
    """Ставит иконку окна/панели задач. Пробует .ico (лучшее качество на Windows),
    затем .png через iconphoto (кроссплатформенный запасной вариант)."""
    ico_path = os.path.join(assets_dir, "app_icon.ico")
    png_path = os.path.join(assets_dir, "app_icon.png")

    if sys.platform == "win32" and os.path.isfile(ico_path):
        try:
            root.iconbitmap(default=ico_path)
            return
        except tk.TclError:
            pass

    if os.path.isfile(png_path):
        try:
            icon_img = tk.PhotoImage(file=png_path)
            root.iconphoto(True, icon_img)
            root._app_icon_ref = icon_img  # держим ссылку, чтобы не собрал GC
        except tk.TclError:
            pass


# Инициализируем модульные глобальные цвета значением темы по умолчанию,
# чтобы theme.BG и т.д. были валидными строками ещё до первого apply_theme()
# (например, если какой-то виджет создаётся до вызова apply_theme).
_apply_palette_globals(PALETTES[DEFAULT_THEME])
