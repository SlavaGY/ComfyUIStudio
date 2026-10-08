"""
Точка входа `python -m comfyui_studio.imagine_pony` (по образцу
comfyui_studio/imagine/__main__.py).

    --host / --port          где слушает сам Imagine Pony (по умолчанию
                              127.0.0.1:7862 -- Imagine на 7860, Remote на 7861)
    --comfy-host/--comfy-port адрес уже запущенного ComfyUI; без них
                              берётся из config.json
                              (%APPDATA%\\ComfyUIStudio\\imagine_pony\\)
"""

import argparse
import os
import sys

IMAGINE_PONY_CLI_FLAG = "--imagine-pony-subprocess"


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="python -m comfyui_studio.imagine_pony")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7862)
    p.add_argument("--comfy-host", default=None)
    p.add_argument("--comfy-port", type=int, default=None)
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    # ДО импорта backend.main: config_store читает их при каждом чтении
    # конфига, но так надёжнее, чем менять окружение после старта.
    if args.comfy_host:
        os.environ["IMAGINE_PONY_COMFY_HOST"] = args.comfy_host
    if args.comfy_port:
        os.environ["IMAGINE_PONY_COMFY_PORT"] = str(args.comfy_port)

    import uvicorn

    from .backend.main import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
