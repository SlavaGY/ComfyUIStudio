"""
Запуск ComfyUI по запросу с телефона -- продолжение этапа "Запуск
приложений через Remote" (§Этап 6.4 дорожной карты). Раньше
`app_launcher.py` только ПРОВЕРЯЛ, поднят ли ComfyUI, и отказывался
запускать Imagine с понятной ошибкой, если нет -- без автозапуска (см.
"Живой баг и фикс" там же: Imagine открывался, но был бесполезен, пока
ComfyUI не подняли вручную через Studio).

Оказалось, что автозапуск ComfyUI ВОЗМОЖЕН без нарушения инварианта
"Remote никогда не импортирует Qt": вся подготовка запуска
(`launcher.core.config.load_config/build_extra_launch_args/
prepare_launch_script`) на поверку Qt-независима -- Qt-зависим только
сам `launcher.core.comfy_process.ComfyProcess`, и то лишь из-за моста
живого лога в Qt-панель настроек (`ProcessLogBridge`), который Remote
всё равно не может использовать (у него нет Qt-окна, куда приземлять
сигналы). Здесь -- Qt-независимый аналог: та же подготовка конфига и
временного .bat, что и в `_on_launch()`
(`launcher/ui/launcher_window.py`), тот же `subprocess.Popen`, что и в
`ComfyProcess.start()`, просто без `ProcessLogBridge`/`_LogReaderThread`
-- вывод процесса логируется в файл напрямую, тем же приёмом, что
`ImagineProcess._log_exit()` в imagine_process.py (см. его докстринг).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Optional

from ..launcher.core.comfy_api import ComfyAPIClient
from ..launcher.core.config import build_extra_launch_args, load_config, prepare_launch_script
from ..launcher.core.logging_setup import log

_lock = threading.Lock()
_process: Optional[subprocess.Popen] = None
# Сообщение о ПОСЛЕДНЕЙ неудаче автозапуска (напр. ComfyUI сразу упал
# после старта) -- POST /start к этому моменту уже давно ответил 202,
# так что единственный способ показать это на телефоне -- отдавать
# через GET /apps (см. app_launcher.imagine_last_error() и AppEntry.error).
_last_error: Optional[str] = None

# Один инстанс на процесс -- по тому же принципу, что и в app_launcher.py.
_comfy_client = ComfyAPIClient()


def is_comfyui_running(port: Optional[int] = None) -> bool:
    cfg = load_config()
    p = port or cfg.get("port")
    return bool(p) and _comfy_client.is_available(port=p)


def comfyui_status(port: Optional[int] = None) -> str:
    """"running" | "starting" | "stopped" -- тот же принцип, что
    app_launcher.imagine_status(). `port` -- см. докстринг start_comfyui()
    про то, почему вызывающая сторона (app_launcher.py) всегда передаёт
    `runtime.comfy_port`, а не полагается на cfg["port"] по умолчанию."""
    if is_comfyui_running(port):
        return "running"
    if _process is not None and _process.poll() is None:
        return "starting"
    return "stopped"


def last_error() -> Optional[str]:
    return _last_error


def start_comfyui(port: Optional[int] = None) -> None:
    """Бросает RuntimeError с понятным сообщением, если ComfyUI ещё не
    настроен вовсе (пустая/несуществующая папка portable, не выбран
    скрипт запуска) -- та же диагностика, которую пользователь увидел
    бы, попытавшись нажать "Старт" в Studio с пустыми настройками.

    `port`, если передан (см. app_launcher.py -- всегда передаёт
    `runtime.comfy_port`), ПЕРЕОПРЕДЕЛЯЕТ cfg["port"] из config.json --
    единственный источник истины о том, на каком порту ComfyUI ждёт
    Imagine, должен быть тот же `runtime.comfy_port`, которым уже
    пользуется весь остальной Remote (см. state.py), а не то, что
    сейчас лежит в config.json -- он мог измениться в настройках Studio
    уже ПОСЛЕ того, как Remote запустился с прежним значением (Remote
    требует перезапуска "Удалённого доступа", чтобы подхватить новый
    порт, см. §0 дорожной карты) -- без этого override можно было бы
    поднять ComfyUI на одном порту, а Imagine продолжил бы стучаться на
    другой (тот же класс бага, что уже случился один раз, только
    сдвинутый на слой ниже)."""
    global _process, _last_error
    with _lock:
        if comfyui_status(port) in ("running", "starting"):
            return

        cfg = dict(load_config())
        if port:
            cfg["port"] = port
        root_path = cfg.get("root_path")
        script = cfg.get("script")
        if not root_path or not os.path.isdir(root_path):
            raise RuntimeError(
                "Папка ComfyUI portable не настроена в Studio -- откройте "
                "Studio на ПК и укажите её в настройках, прежде чем "
                "запускать ComfyUI с телефона."
            )
        if not script:
            raise RuntimeError(
                "Скрипт запуска ComfyUI не выбран в настройках Studio -- "
                "откройте Studio на ПК и выберите run_*.bat."
            )

        try:
            launch_script = prepare_launch_script(root_path, script, build_extra_launch_args(cfg))
        except OSError as e:
            raise RuntimeError(f"Не удалось подготовить скрипт запуска ComfyUI: {e}")

        env = os.environ.copy()
        env_overrides = cfg.get("env_vars")
        if env_overrides:
            env.update(env_overrides)

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        log.info("Запуск ComfyUI через Remote: %s (cwd=%s)", launch_script, root_path)
        _last_error = None
        try:
            proc = subprocess.Popen(
                ["cmd.exe", "/c", launch_script],
                cwd=root_path,
                creationflags=creationflags,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as e:
            # НОВОЕ по сравнению с ComfyProcess/ImagineProcess (те не
            # оборачивают Popen) -- здесь ошибка иначе улетела бы наружу
            # как голый необработанный OSError мимо HTTPException в
            # routes/apps.py (тот ловит только RuntimeError) и дошла бы
            # до телефона как безликий 500 без текста.
            raise RuntimeError(f"Не удалось запустить ComfyUI: {e}")
        _process = proc
        watcher = threading.Thread(target=_log_exit, args=(proc,), daemon=True)
        watcher.start()


def _log_exit(proc: subprocess.Popen) -> None:
    global _last_error
    output = ""
    try:
        output = proc.stdout.read() if proc.stdout else ""
    except Exception:
        log.exception("Не удалось прочитать вывод процесса ComfyUI (запущен через Remote)")
    finally:
        if proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass
    code = proc.wait()
    if code == 0:
        log.info("ComfyUI (PID %s, запущен через Remote) завершился, код выхода 0", proc.pid)
    else:
        tail = output[-4000:] if output else "(процесс не вывел ничего в stdout/stderr)"
        log.error(
            "ComfyUI (PID %s, запущен через Remote) завершился с кодом %s. Вывод:\n%s",
            proc.pid, code, tail,
        )
        # Короткая версия для телефона -- полный вывод только в лог-файл
        # Studio на ПК, как и у обычного запуска через UI.
        _last_error = (
            f"ComfyUI завершился с кодом {code} сразу после запуска -- "
            "проверьте лог ComfyUI на ПК (вкладка настроек в Studio)."
        )


def clear_state() -> None:
    """Только для тестов -- см. app_launcher.clear_state()."""
    global _process, _last_error
    _process = None
    _last_error = None
