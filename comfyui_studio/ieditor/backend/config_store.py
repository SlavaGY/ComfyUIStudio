"""Persistent form and job history, separate from the other Studio apps."""

import os
import threading
from pathlib import Path

from comfyui_studio import app_paths
from comfyui_studio.jsonfile import atomic_write_json, load_json

DATA_DIR = Path(os.environ.get("IEDITOR_DATA_DIR") or Path(app_paths.studio_data_dir()) / "ieditor")
_lock = threading.Lock()


def connection():
    return {
        "host": os.environ.get("IEDITOR_COMFY_HOST", "127.0.0.1"),
        "port": int(os.environ.get("IEDITOR_COMFY_PORT", "8188")),
    }


def load_state():
    with _lock:
        data = load_json(DATA_DIR / "state.json").data
        return data if isinstance(data, dict) else {}


def save_state(state):
    with _lock:
        atomic_write_json(DATA_DIR / "state.json", state)


def load_jobs():
    with _lock:
        data = load_json(DATA_DIR / "jobs.json", expected_type=list).data
        return data if isinstance(data, list) else []


def remember_job(job):
    with _lock:
        path = DATA_DIR / "jobs.json"
        data = load_json(path, expected_type=list).data
        jobs = data if isinstance(data, list) else []
        atomic_write_json(path, [job, *jobs][:30])


def save_result(prompt_id, result):
    with _lock:
        path = DATA_DIR / "jobs.json"
        data = load_json(path, expected_type=list).data
        if not isinstance(data, list):
            return
        for job in data:
            if job.get("prompt_id") == prompt_id:
                job["result"] = result
                atomic_write_json(path, data)
                return
