# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec для сборки в папку (onedir).
# Собирать: pyinstaller build.spec   (после pip install pyinstaller)
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

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('themes', 'themes'),   # *.qss темы — обязательно, иначе темы не найдутся
        ('assets', 'assets'),   # иконка приложения
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
    icon='assets/app_icon.ico',
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
