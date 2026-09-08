"""
Точка входа `python -m comfyui_studio.remote` — по образцу
comfyui_studio/imagine/__main__.py (тот же принцип диспетчеризации
скрытым CLI-флагом из корневого main.py, см. REMOTE_CLI_FLAG ниже и
launcher/core/remote_process.py).

Аргументы командной строки — то, чем launcher/core/remote_process.py
параметризует запуск (см. его докстринг):

    --host           адрес, на котором слушает сам Remote (по
                      умолчанию 127.0.0.1; для реального LAN-доступа с
                      телефона -- 0.0.0.0, см. §8 "LAN Release")
    --port           порт Remote (по умолчанию 7861 -- на единицу
                      больше порта Imagine по умолчанию, 7860, чтобы
                      оба процесса могли быть подняты одновременно на
                      одной машине без конфликта портов "из коробки")
    --comfy-host     адрес уже поднятого Studio ComfyUI (для чтения
                      статуса/очереди через ComfyAPIClient — тот
                      всегда обращается к 127.0.0.1, см. его модуль;
                      принимается для симметрии с Imagine и на будущее)
    --comfy-port     порт уже поднятого Studio ComfyUI
    --imagine-port   порт уже поднятого (или потенциально поднимаемого)
                      Imagine -- для is_imagine_available() в
                      routes/system.py
    --dev            зарезервировано на будущее (см. --dev у Imagine) —
                      этап 1 Remote его никак не использует, но
                      принимается уже сейчас, чтобы CLI-контракт не
                      пришлось менять при появлении дев-режима позже

При самостоятельном запуске (не из Studio, например при разработке
Remote отдельно) все аргументы, кроме --host/--port, необязательны --
Remote просто не будет знать про ComfyUI/Imagine (comfy_port/
imagine_port остаются None, см. state.py), и /status честно отдаст
comfyui_running=False/imagine_running=False.
"""

from __future__ import annotations

import argparse
import sys

# Скрытый флаг для диспетчеризации того же frozen exe — см. main.py в
# корне репозитория и аналогичный IMAGINE_CLI_FLAG в
# comfyui_studio/imagine/__main__.py. Импортируется отдельно от main()
# этого модуля, чтобы main.py мог проверить sys.argv[1] максимально
# рано, не затягивая тяжёлые импорты (uvicorn/fastapi) раньше времени.
REMOTE_CLI_FLAG = "--remote-subprocess"


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="python -m comfyui_studio.remote")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7861)
    parser.add_argument("--comfy-host", default="127.0.0.1")
    parser.add_argument("--comfy-port", type=int, default=None)
    parser.add_argument("--imagine-port", type=int, default=None)
    parser.add_argument("--dev", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    # ВАЖНО: выставляется ДО импорта .app (который импортирует routes/,
    # читающие state.runtime на уровне обработчиков запросов, а не на
    # уровне модуля -- порядок здесь не так критичен, как у Imagine
    # (config_store.py), но сохраняем тот же порядок операций для
    # единообразия и на случай будущих модулей, читающих runtime раньше).
    from .state import runtime

    runtime.comfy_host = args.comfy_host
    runtime.comfy_port = args.comfy_port
    runtime.imagine_port = args.imagine_port
    # НОВОЕ (этап 5, mDNS) -- собственный адрес/порт Remote нужны
    # mdns.py (см. state.py) ДО того, как app.py поднимет lifespan.
    runtime.host = args.host
    runtime.port = args.port

    import uvicorn

    from .app import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
