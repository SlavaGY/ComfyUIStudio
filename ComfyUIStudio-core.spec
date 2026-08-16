# -*- mode: python ; coding: utf-8 -*-
#
# Профиль CORE-сборки ComfyUI Studio — этап 3 дорожной карты
# рефакторинга ("Влияние на сборку"): Launcher + Prompt Builder +
# PromptVault БЕЗ torch/sentence-transformers/transformers/tokenizers.
#
# PromptVault в этой сборке открывается и полностью работает — обычный
# текстовый поиск, фильтры, галерея, метаданные, статистика. Чекбокс
# "Enable semantic search" в его настройках задизейблен с пояснением
# (см. comfyui_studio/promptvault/ui/settings_window.py,
# _apply_semantic_search_availability, и
# comfyui_studio/promptvault/core/embedding.py, is_available/
# _torch_version_compatible) — это тот же самый механизм, который уже
# отрабатывал сценарий "торч старой версии" (is_available/_load_failed),
# этап 3 лишь распространил его на "торча нет вообще" и подключил к UI.
#
# `excludes` ниже — это НЕ просто "эти пакеты не установлены в venv
# сборки" (тогда PyInstaller и так бы их не нашёл): в комментариях
# исходного .spec отдельно отмечено, что PyInstaller иногда затягивает
# transformers/tokenizers ТРАНЗИТИВНО через PromptVault, даже если сам
# код их напрямую не импортирует на всех путях выполнения — explicit
# excludes страхует от этого и делает исключение проверяемым
# (build упадёт заметно, а не молча раздуется, если где-то в коде
# появится безусловный "import torch" на уровне модуля).
#
# Собирайте core-профиль в venv, где torch/sentence-transformers/
# transformers НЕ установлены (pip install . — без [promptvault]) —
# иначе PyInstaller всё равно найдёт и упакует их несмотря на excludes,
# если что-то в графе импортов их безусловно тянет.
#
# См. также ComfyUIStudio-full.spec — тот же набор datas и main.py,
# полный комплект с семантическим поиском.

datas = [
    ('assets', 'assets'),
    ('comfyui_studio/themes', 'comfyui_studio/themes'),
    ('comfyui_studio/prompt_builder/assets', 'comfyui_studio/prompt_builder/assets'),
    ('comfyui_studio/prompt_builder/themes', 'comfyui_studio/prompt_builder/themes'),
    ('comfyui_studio/promptvault/resources', 'comfyui_studio/promptvault/resources'),
    ('comfyui_studio/promptvault/themes', 'comfyui_studio/promptvault/themes'),
]
binaries = []
hiddenimports = []

# см. комментарий в шапке файла — явное исключение, а не просто расчёт
# на отсутствие пакетов в venv сборки
excludes = ["torch", "sentence_transformers", "transformers", "tokenizers"]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ComfyUIStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ComfyUIStudio',
)
