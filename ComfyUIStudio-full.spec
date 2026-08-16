# -*- mode: python ; coding: utf-8 -*-
#
# Профиль ПОЛНОЙ сборки ComfyUI Studio (Launcher + Prompt Builder +
# PromptVault, включая семантический поиск) — этап 3 дорожной карты
# рефакторинга ("Влияние на сборку").
#
# Замена корневого ComfyUIStudio.spec, который был написан ДО переноса
# трёх инструментов под общее пространство имён comfyui_studio/ (этапы
# 1–2) и с тех пор не обновлялся: он собирал datas из tools/prompt_builder
# и tools/promptvault/app — путей, которых с этапа 2 больше нет
# (comfyui_studio/prompt_builder/config.py.THEMES_DIR и
# comfyui_studio/promptvault/config.py._APP_DIR при frozen=True сами
# ищут свои файлы данных по путям вида
# _MEIPASS/comfyui_studio/prompt_builder/... и
# _MEIPASS/comfyui_studio/promptvault/... — см. комментарии в этих
# файлах; datas ниже зеркалят именно эту раскладку, а не старую).
#
# Второй профиль — ComfyUIStudio-core.spec — тот же набор datas и
# main.py, но БЕЗ torch/sentence-transformers/transformers (см. его
# комментарии). Этот файл (full) — полный комплект, как было раньше.

from PyInstaller.utils.hooks import collect_all

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

# sentence-transformers/transformers/tokenizers тянут немало data-файлов
# и сабмодулей, которые PyInstaller не всегда видит статическим анализом
# импортов -- как и в исходном .spec, веса самой модели эмбеддинга (см.
# comfyui_studio/promptvault/core/embedding.py, MODEL_NAME) сюда не
# входят: они кэшируются в ~/.cache/huggingface при первом запуске, а
# не бандлятся в exe.
for _pkg in ("sentence_transformers", "transformers", "tokenizers"):
    _datas, _binaries, _hiddenimports = collect_all(_pkg)
    datas += _datas
    binaries += _binaries
    hiddenimports += _hiddenimports


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
