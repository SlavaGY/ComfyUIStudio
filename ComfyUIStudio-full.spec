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
    # Imagine (comfyui_studio/imagine/ -- вариант запуска ComfyUI, см.
    # launcher/core/imagine_process.py) -- фронтенд без сборки (чистые
    # .html/.css/.js, см. backend/main.py _STATIC_DIR) и бандловый
    # workflow_template.json (см. backend/workflow.py _ASSETS_DIR) --
    # ни то ни другое не .py, PyInstaller не подхватит их сам.
    ('comfyui_studio/imagine/static', 'comfyui_studio/imagine/static'),
    ('comfyui_studio/imagine/assets', 'comfyui_studio/imagine/assets'),
]
binaries = []
hiddenimports = [
    # embedding_worker.py/embedding_ipc.py импортируются лениво/условно
    # (внутри if в main.py — см. диспетчеризацию в режим подпроцесса
    # воркера эмбеддингов там) — PyInstaller обычно находит такие через
    # обычный AST-анализ и без этого, но раз от их отсутствия в сборке
    # тихо сломался бы весь семантический поиск (а не упал с понятной
    # ошибкой), лучше перечислить явно, а не полагаться на анализ.
    "comfyui_studio.promptvault.core.embedding_worker",
    "comfyui_studio.promptvault.core.embedding_ipc",
    # comfyui_studio.imagine.__main__ — аналогично: импортируется лениво
    # внутри if в main.py (диспетчеризация IMAGINE_CLI_FLAG), а сам он
    # изнутри лениво импортирует uvicorn/backend.main (см. его main()) —
    # оба уровня лени PyInstaller не обязан пройти статическим анализом.
    "comfyui_studio.imagine.__main__",
    "comfyui_studio.imagine.backend.main",
]

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

# Imagine (см. datas/hiddenimports выше) -- uvicorn/fastapi/starlette
# лениво импортируют часть своих протокольных бэкендов и plugin-подобных
# субмодулей (uvicorn.protocols.*, uvicorn.lifespan.*, uvicorn.loops.*)
# так, что обычный AST-анализ PyInstaller их не всегда находит --
# collect_all вместо точечных hiddenimports, тем же приёмом, что и для
# sentence_transformers/transformers/tokenizers выше. "multipart" --
# именно так называется импортируемый модуль пакета python-multipart
# (см. IMAGINE_REQUIRED_MODULES в launcher/core/imagine_process.py).
for _pkg in ("fastapi", "starlette", "uvicorn", "multipart"):
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

# UPX ломает Qt6/Chromium-бинарники (это не только Qt-плагины, которые
# PyInstaller исключает из UPX сам с версии 4.3 — Qt*.dll и exe-хелперы
# движка WebEngine в эту автоматику не попадают). Симптом на практике:
# встроенный интерфейс ComfyUI после сборки .exe начинает моргать даже в
# статике (без UPX — из исходников python main.py — стабильно). См.
# https://github.com/upx/upx/issues/107 и рекомендацию самой
# документации PyInstaller исключать "Qt*.dll" через upx_exclude.
UPX_EXCLUDE = [
    "Qt6*.dll",
    "libEGL.dll",
    "libGLESv2.dll",
    "d3dcompiler_47.dll",
    "opengl32sw.dll",
    "QtWebEngineProcess.exe",
]

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
    upx_exclude=UPX_EXCLUDE,
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
    upx_exclude=UPX_EXCLUDE,
    name='ComfyUIStudio',
)
