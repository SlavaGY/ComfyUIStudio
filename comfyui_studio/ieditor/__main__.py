"""Standalone and bundled entry point for IEditor."""

import argparse
import os
import sys

IEDITOR_CLI_FLAG = "--ieditor-subprocess"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m comfyui_studio.ieditor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7865)
    parser.add_argument("--comfy-host", default="127.0.0.1")
    parser.add_argument("--comfy-port", type=int, default=8188)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    os.environ["IEDITOR_COMFY_HOST"] = args.comfy_host
    os.environ["IEDITOR_COMFY_PORT"] = str(args.comfy_port)

    import uvicorn

    from .backend.main import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
