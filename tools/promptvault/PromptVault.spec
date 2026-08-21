# -*- mode: python ; coding: utf-8 -*-
# Standalone-сборка ОДНОГО PromptVault, в обход единого build_exe.bat в
# корне репозитория (см. его комментарии и ComfyUIStudio-full.spec).
#
# Обычно этот файл пересоздаётся с нуля самим build.bat (тот вызывает
# pyinstaller напрямую флагами, а не через `pyinstaller
# PromptVault.spec`, и удаляет старый .spec перед сборкой) -- но
# держим его тоже актуальным на случай прямого запуска
# `pyinstaller tools/promptvault/PromptVault.spec` в обход build.bat.
#
# tools/promptvault/ -- ЛЕГАСИ-папка (см. дорожную карту рефакторинга,
# этап 2): исходники PromptVault перенесены в comfyui_studio/promptvault/
# под общее пространство имён комплекта. SPECPATH (стандартная
# PyInstaller-переменная -- абсолютный путь папки с этим .spec) лежит в
# tools/promptvault/, поэтому ROOT_DIR ниже поднимается на два уровня
# вверх до корня репозитория, откуда и берутся все datas/Analysis-пути.
import os

ROOT_DIR = os.path.abspath(os.path.join(SPECPATH, '..', '..'))
PROMPTVAULT_SRC = os.path.join(ROOT_DIR, 'comfyui_studio', 'promptvault')

from PyInstaller.utils.hooks import collect_all

# Назначение (второй элемент каждого кортежа) ЗЕРКАЛИТ пакетный путь
# comfyui_studio/promptvault/... неспроста -- comfyui_studio/promptvault/
# config.py и .../themes/theme_manager.py находят свои файлы через
# Path(__file__).resolve().parent / "resources" (см. ICON_PATH/
# TRANSLATIONS_DIR/THEMES_DIR в config.py), а PyInstaller в one-folder
# режиме подставляет __file__ замороженных модулей как путь ВНУТРИ
# папки со сборкой (_internal\... в PyInstaller 6+) с тем же
# относительным пакетным путём -- см. build.bat рядом для подробностей
# (та же раскладка datas используется и в корневом
# ComfyUIStudio-full.spec).
datas = [
    (os.path.join(PROMPTVAULT_SRC, 'resources'), os.path.join('comfyui_studio', 'promptvault', 'resources')),
    (os.path.join(PROMPTVAULT_SRC, 'themes'), os.path.join('comfyui_studio', 'promptvault', 'themes')),
]
binaries = []
hiddenimports = [
    # embedding_worker.py/embedding_ipc.py импортируются лениво/условно
    # (в самом верху comfyui_studio/promptvault/main.py, до остальных
    # импортов -- см. диспетчеризацию в режим подпроцесса воркера
    # эмбеддингов там) -- PyInstaller обычно находит такие и без этого
    # через обычный AST-анализ, но раз от их отсутствия в сборке тихо
    # сломался бы весь семантический поиск (а не упал с понятной
    # ошибкой), лучше перечислить явно, а не полагаться на анализ.
    "comfyui_studio.promptvault.core.embedding_worker",
    "comfyui_studio.promptvault.core.embedding_ipc",
]
tmp_ret = collect_all('sentence_transformers')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('transformers')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('tokenizers')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    [os.path.join(PROMPTVAULT_SRC, 'main.py')],
    # ROOT_DIR (не tools/promptvault/) на pathex -- иначе PyInstaller не
    # найдёт пакет `comfyui_studio`, который лежит в корне репозитория,
    # а не под tools/.
    pathex=[ROOT_DIR],
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
    name='PromptVault',
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
    icon=[os.path.join(PROMPTVAULT_SRC, 'resources', 'icon.ico')],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PromptVault',
)
