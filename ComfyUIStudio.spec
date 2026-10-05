# -*- mode: python ; coding: utf-8 -*-
#
# Единый профиль сборки ComfyUI Studio (Launcher + Prompt Builder +
# PromptVault) в один exe-комплект.
#
# Раньше здесь были два профиля — ComfyUIStudio-full.spec (с
# семантическим поиском в PromptVault, torch/sentence-transformers/
# transformers/tokenizers) и ComfyUIStudio-core.spec (без него,
# explicit excludes) — семантический поиск с тех пор полностью удалён
# из исходников PromptVault, так что деление на профили больше не
# нужно: это единственный и теперь снова единственный .spec, как было
# до этапа 3 дорожной карты рефакторинга.
#
# comfyui_studio/prompt_builder/config.py.THEMES_DIR и
# comfyui_studio/promptvault/config.py._APP_DIR при frozen=True сами
# ищут свои файлы данных по путям вида
# _MEIPASS/comfyui_studio/prompt_builder/... и
# _MEIPASS/comfyui_studio/promptvault/... — см. комментарии в этих
# файлах; datas ниже зеркалят именно эту раскладку.

from PyInstaller.utils.hooks import collect_all

datas = [
    ('assets', 'assets'),
    # Общая папка тем: её используют все три приложения (themes/base.py);
    # отдельных themes у Prompt Builder и PromptVault больше нет (этап R8).
    ('comfyui_studio/themes', 'comfyui_studio/themes'),
    ('comfyui_studio/prompt_builder/assets', 'comfyui_studio/prompt_builder/assets'),
    ('comfyui_studio/promptvault/resources', 'comfyui_studio/promptvault/resources'),
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
    # comfyui_studio.imagine.__main__ — импортируется лениво внутри if в
    # main.py (диспетчеризация IMAGINE_CLI_FLAG), а сам он изнутри лениво
    # импортирует uvicorn/backend.main (см. его main()) — оба уровня лени
    # PyInstaller не обязан пройти статическим анализом.
    "comfyui_studio.imagine.__main__",
    "comfyui_studio.imagine.backend.main",
    # comfyui_studio.remote.__main__ -- та же двухуровневая лень, что и у
    # Imagine выше (диспетчеризация REMOTE_CLI_FLAG в main.py, а изнутри
    # __main__.main() лениво импортирует uvicorn/.app), см.
    # ComfyUIStudio_Remote_Roadmap.md, этап 1, launcher/core/remote_process.py.
    "comfyui_studio.remote.__main__",
    "comfyui_studio.remote.app",
]

# Imagine/Remote -- uvicorn/fastapi/starlette лениво импортируют часть
# своих протокольных бэкендов и plugin-подобных субмодулей
# (uvicorn.protocols.*, uvicorn.lifespan.*, uvicorn.loops.*) так, что
# обычный AST-анализ PyInstaller их не всегда находит -- collect_all
# вместо точечных hiddenimports. "multipart" -- именно так называется
# импортируемый модуль пакета python-multipart (см.
# IMAGINE_REQUIRED_MODULES в launcher/core/imagine_process.py).
# "websockets" — рантайм-зависимость самого Imagine
# (comfyui_studio/imagine/backend/progress_forwarder.py), не только
# тестов (см. pyproject.toml). "httpx"/"httpcore" -- reverse-proxy
# Remote к Imagine (comfyui_studio/remote/imagine_proxy.py); httpcore --
# его внутренний HTTP-транспорт с несколькими backend'ами
# (asyncio/anyio), тот же риск ленивых импортов мимо AST-анализа, что и
# у остальных пакетов в этом цикле. "zeroconf" -- mDNS-объявление
# (comfyui_studio/remote/mdns.py), тянет собственные платформенные
# модули определения сетевых интерфейсов.
for _pkg in ("fastapi", "starlette", "uvicorn", "multipart", "websockets", "httpx", "httpcore", "zeroconf"):
    _datas, _binaries, _hiddenimports = collect_all(_pkg)
    datas += _datas
    binaries += _binaries
    hiddenimports += _hiddenimports

# `google-auth[requests]` (fcm.py, §Этап 6.5, push-уведомления) сам по
# себе уже один раз давал живой баг именно на уровне зависимостей (см.
# pyproject.toml -- забыли extras "[requests]"), и есть все основания
# ожидать второго раунда той же болезни уже на уровне PyInstaller:
# "google.auth"/"google.oauth2" -- PEP 420 namespace-пакеты, а сами
# модули (`transport.requests`, `crypt.rsa` и т.п.) импортируются
# ВНУТРИ google-auth условно/лениво в зависимости от доступных
# бэкендов -- то есть ровно тот же паттерн "обычный AST-анализ
# PyInstaller не найдёт сам", что и у fastapi/uvicorn/httpx выше.
# `collect_all("google.auth")` для namespace-пакета ненадёжен (в
# отличие от обычных пакетов в цикле выше) -- поэтому явные
# hiddenimports вместо него; "cryptography" -- обычный (не namespace)
# пакет со своими бинарными расширениями (hazmat backend), под него
# collect_all уместен и безопасен, как и для остальных пакетов цикла
# выше.
hiddenimports += [
    "google.auth",
    "google.auth.transport.requests",
    "google.auth._helpers",
    "google.auth.jwt",
    "google.auth.crypt",
    "google.auth.crypt.rsa",
    "google.auth.crypt._cryptography_rsa",
    "google.oauth2",
    "google.oauth2.service_account",
    "google.oauth2._client",
    "google.oauth2.credentials",
    "cachetools",
    "pyasn1",
    "pyasn1_modules",
    "rsa",
    "six",
]
for _pkg in ("cryptography",):
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
