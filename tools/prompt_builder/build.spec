# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec для сборки в папку (onedir).
# Собирать: pyinstaller build.spec   (после pip install pyinstaller,
# см. build_windows.bat рядом, который это и делает)
# Даст dist/PromptConfigEditor/PromptConfigEditor.exe — сам exe маленький
# и запускается мгновенно, рядом лежат его зависимости отдельными файлами.
#
# Раньше здесь была onefile-сборка (один самораспаковывающийся .exe).
# Она переключена на onedir по двум причинам:
#   1. resolve_external_launch() в comfyui_launcher.py ищет готовый exe
#      по пути dist/<имя>/<имя>.exe — это путь onedir-сборки. Настоящая
#      onefile-сборка PyInstaller кладёт exe плоско, dist/<имя>.exe, без
#      подпапки, так что лаунчер его попросту не находил.
#   2. Onefile-сборка на каждый запуск разворачивает весь рантайм во
#      временную папку через отдельный процесс-bootloader и держит её,
#      пока сам процесс жив, а после закрытия окна должна ещё раз
#      дождаться и подчистить эту папку — именно на этом шаге bootloader
#      иногда зависает (особенно если антивирус в моменте держит файлы
#      во временной папке залоченными), и тогда процесс остаётся висеть
#      в памяти, хотя окно уже закрыто. Onedir-сборка так не делает —
#      exe запускается напрямую, без промежуточного bootloader-процесса.
#
# tools/prompt_builder/ -- ЛЕГАСИ-папка (см. дорожную карту
# рефакторинга, этап 2): исходники Prompt Builder перенесены в
# comfyui_studio/prompt_builder/ под общее пространство имён комплекта.
# SPECPATH (стандартная PyInstaller-переменная -- абсолютный путь папки
# с этим .spec) лежит в tools/prompt_builder/, поэтому ROOT_DIR ниже
# поднимается на два уровня вверх до корня репозитория, откуда и берутся
# все datas/Analysis-пути.
import os

ROOT_DIR = os.path.abspath(os.path.join(SPECPATH, '..', '..'))
PROMPT_BUILDER_SRC = os.path.join(ROOT_DIR, 'comfyui_studio', 'prompt_builder')

a = Analysis(
    [os.path.join(PROMPT_BUILDER_SRC, 'main.py')],
    # ROOT_DIR (не tools/prompt_builder/) на pathex -- иначе PyInstaller
    # не найдёт пакет `comfyui_studio`, который лежит в корне
    # репозитория, а не под tools/.
    pathex=[ROOT_DIR],
    binaries=[],
    datas=[
        # Назначение (второй элемент каждого кортежа) ЗЕРКАЛИТ пакетный
        # путь comfyui_studio/prompt_builder/... неспроста --
        # theme_manager.py (resource_base()) ищет свои темы/иконку
        # через Path(sys._MEIPASS) / "comfyui_studio" / "prompt_builder"
        # при запуске из-под PyInstaller — см. его комментарии; если
        # сплющить назначение в корень, resource_base() перестанет
        # находить themes/assets рядом с собой. Та же раскладка datas
        # используется и в корневом ComfyUIStudio-full.spec.
        (os.path.join(PROMPT_BUILDER_SRC, 'themes'), os.path.join('comfyui_studio', 'prompt_builder', 'themes')),
        (os.path.join(PROMPT_BUILDER_SRC, 'assets'), os.path.join('comfyui_studio', 'prompt_builder', 'assets')),
    ],
    hiddenimports=[],
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
    name='PromptConfigEditor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # без чёрного консольного окна на Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(PROMPT_BUILDER_SRC, 'assets', 'app_icon.ico'),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PromptConfigEditor',
)
