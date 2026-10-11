"""
Точка входа `python -m comfyui_studio.rvc` -- приложение Studio для Ultimate RVC.

ВАЖНО: запускается питоном из venv Ultimate RVC (а не питоном Studio) -- см.
launcher/core/rvc_process.py. Пакет comfyui_studio при этом подкладывается через
PYTHONPATH; поэтому внутри comfyui_studio/rvc нельзя импортировать ничего из
Qt и остального кода Studio (проверяет tests/launcher/test_rvc_app_isolation.py).

Аргументы (их передаёт лаунчер):
    --host       адрес (по умолчанию 127.0.0.1)
    --port       порт (по умолчанию 7863)
    --data-dir   где держать загрузки и тексты (по умолчанию ~/.studio_rvc)
"""

import argparse
import os
import sys


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="python -m comfyui_studio.rvc")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7863)
    parser.add_argument("--data-dir", default=None)
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    # Читается backend.main на уровне модуля -- выставляем ДО его импорта.
    if args.data_dir:
        os.environ["STUDIO_RVC_DATA_DIR"] = args.data_dir

    import uvicorn

    from .backend.main import app

    print(f"Studio RVC: http://{args.host}:{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
