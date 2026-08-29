"""
Точка входа `python -m comfyui_studio.imagine` — по образцу
prompt_builder/__main__.py и promptvault/__main__.py, но поднимает
uvicorn вместо QApplication (Imagine — веб-инструмент, открывается во
встроенном браузере лаунчера через launcher/core/imagine_process.py,
а не как окно этого же Qt-процесса).

Аргументы командной строки — то, чем launcher/core/imagine_process.py
параметризует запуск (см. его докстринг):

    --host           адрес, на котором слушает сам Imagine (по
                      умолчанию 127.0.0.1)
    --port           порт Imagine (по умолчанию 7860)
    --comfy-host     адрес уже поднятого Studio ComfyUI
    --comfy-port     порт уже поднятого Studio ComfyUI
    --dev            включить дев-режим (аналог run.bat dev у
                      самостоятельного запуска, см. backend/main.py)

При самостоятельном запуске (не из Studio) все аргументы необязательны:
--comfy-host/--comfy-port просто не передаются, и Imagine продолжает
использовать то, что уже сохранено в его собственном config.json
(редактируется на вкладке "Подключение" дев-режима), как раньше.
"""

import argparse
import os
import sys

# Скрытый флаг для диспетчеризации того же frozen exe (см. main.py в
# корне репозитория, ту же роль для воркера эмбеддингов PromptVault
# играет WORKER_CLI_FLAG в comfyui_studio/promptvault/core/
# embedding_ipc.py) -- собранный ComfyUIStudio.exe, запущенный с этим
# флагом первым аргументом, становится Imagine-подпроцессом вместо
# обычного GUI (см. launcher/core/imagine_process.py,
# resolve_imagine_launch()). Импортируется отдельно от main() этого
# модуля, чтобы main.py мог проверить sys.argv[1] максимально рано, не
# затягивая тяжёлые импорты (uvicorn/fastapi) раньше времени.
IMAGINE_CLI_FLAG = "--imagine-subprocess"


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="python -m comfyui_studio.imagine")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--comfy-host", default=None)
    parser.add_argument("--comfy-port", type=int, default=None)
    parser.add_argument("--dev", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    # ВАЖНО: должно быть выставлено ДО импорта backend.main — читает
    # DEV_MODE как переменную окружения на уровне модуля (как и раньше,
    # при запуске через run.bat dev, см. backend/main.py), и сеет
    # comfyui.host/port в конфиг тоже на уровне модуля при импорте
    # (см. _seed_comfy_target_from_env() там же).
    os.environ["IMAGINE_DEV_MODE"] = "1" if args.dev else "0"
    if args.comfy_host:
        os.environ["IMAGINE_COMFY_HOST"] = args.comfy_host
    if args.comfy_port:
        os.environ["IMAGINE_COMFY_PORT"] = str(args.comfy_port)

    import uvicorn

    from .backend.main import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
