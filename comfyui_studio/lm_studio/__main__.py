"""Standalone and frozen-executable entry point; deliberately imports no Qt."""

import argparse
import json
from pathlib import Path

LM_STUDIO_CLI_FLAG = "--studio-lm-studio"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7864)
    parser.add_argument("--config", required=True)
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args(argv)
    import uvicorn

    from .backend import create_app

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    app = create_app(config.get("lm_studio") or {}, Path(args.data_dir))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="info", timeout_graceful_shutdown=5))
    app.state.request_exit = lambda: setattr(server, "should_exit", True)
    server.run()


if __name__ == "__main__":
    main()
