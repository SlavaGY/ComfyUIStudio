"""
Запуск и остановка Remote — REST API для удалённого/мобильного доступа
к Studio (comfyui_studio/remote/, см. ComfyUIStudio_Remote_Roadmap.md,
§0.1/этап 1).

По архитектуре — 1:1 та же схема диспетчеризации, что уже отработана
для Imagine (imagine_process.py) и воркера эмбеддингов PromptVault
(embedding_ipc.py): при frozen=True sys.executable — сам собранный
ComfyUIStudio.exe (отдельного python.exe рядом нет), поэтому вместо
поиска стороннего exe лаунчер запускает САМ СЕБЯ со скрытым флагом
REMOTE_CLI_FLAG — main.py ловит его в самом начале, до создания
QApplication и любых Qt-импортов (см. корневой main.py), и вместо
обычного GUI-запуска вызывает comfyui_studio.remote.__main__.main() с
оставшимися аргументами.

В ОТЛИЧИЕ от ImagineProcess: Remote не привязан к тому, запущен ли
ComfyUI/Imagine прямо сейчас (см. §0 дорожной карты — принцип "Remote
сам решает, что реально доступно"). Включение/выключение Remote в
настройках (см. ui/settings/remote_page.py) полностью независимо от
переключателя "Интерфейс: ComfyUI/Imagine" и от того, запущен ли сам
ComfyUI — Remote можно поднять даже тогда, когда ComfyUI ещё не
запускался вовсе (тогда GET /status просто отдаст comfyui_running=False).
"""

from __future__ import annotations

import json as _json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request

from .constants import PROJECT_ROOT
from .logging_setup import log
from ...remote.__main__ import REMOTE_CLI_FLAG

REMOTE_MODULE_NAME = "comfyui_studio.remote"
# Те же зависимости, что и у Imagine (см. IMAGINE_REQUIRED_MODULES в
# imagine_process.py) -- отдельной группы extras для Remote не заведено
# (см. pyproject.toml, комментарий у группы `imagine`): Remote --
# FastAPI/uvicorn-процесс ровно так же, как Imagine, и появился позже
# него, когда эти зависимости уже были в проекте.
REMOTE_REQUIRED_MODULES = ("fastapi", "uvicorn", "pydantic", "multipart", "httpx", "zeroconf")


def _missing_remote_dependencies():
    """См. аналогичную _missing_imagine_dependencies() в
    imagine_process.py -- тот же приём (importlib.util.find_spec в ТОМ
    ЖЕ интерпретаторе/exe, что и будет запускать Remote), та же причина
    (не запускать процесс, заранее зная, что ImportError оставит только
    голый код выхода 1 без единой подсказки)."""
    import importlib.util
    missing = [m for m in REMOTE_REQUIRED_MODULES if importlib.util.find_spec(m) is None]
    return missing


def resolve_remote_launch(host, port, comfy_host, comfy_port, imagine_port, dev_mode=False):
    """Аналог resolve_imagine_launch() -- возвращает (cmd, cwd, None)
    либо (None, None, error)."""
    extra_args = ["--host", host, "--port", str(port), "--comfy-host", comfy_host]
    if comfy_port:
        extra_args += ["--comfy-port", str(comfy_port)]
    if imagine_port:
        extra_args += ["--imagine-port", str(imagine_port)]
    if dev_mode:
        extra_args.append("--dev")

    missing = _missing_remote_dependencies()
    if missing:
        return None, None, (
            "Не установлены зависимости Remote в текущем окружении: "
            + ", ".join(missing) + ".\n"
            + (
                "Соберите комплект заново — build_exe.bat ставит extras "
                "'imagine' автоматически, Remote использует те же самые "
                "зависимости (см. pyproject.toml)."
                if getattr(sys, "frozen", False) else
                "Выполните `pip install .[imagine]` (fastapi/uvicorn/"
                "pydantic/python-multipart -- отдельной группы extras для "
                "Remote не заведено, см. pyproject.toml) в том же venv, из "
                "которого запущен ComfyUIStudio."
            )
        )

    if getattr(sys, "frozen", False):
        # sys.executable -- сам собранный ComfyUIStudio.exe, см. докстринг
        # модуля и аналогичный участок в imagine_process.py. cwd намеренно
        # не переопределяется -- наследуется от текущего процесса
        # лаунчера, Remote резолвит свои пути (device_store.py) абсолютно,
        # через %APPDATA%, а не относительно cwd.
        return [sys.executable, REMOTE_CLI_FLAG] + extra_args, None, None

    source_entry = os.path.join(PROJECT_ROOT, "comfyui_studio", "remote", "__main__.py")
    if not os.path.isfile(source_entry):
        return None, None, (
            f"Не найден пакет {REMOTE_MODULE_NAME} ({source_entry}) — "
            "похоже, исходники комплекта повреждены или неполные."
        )

    return (
        [sys.executable, "-m", REMOTE_MODULE_NAME] + extra_args,
        PROJECT_ROOT,
        None,
    )


class RemoteProcess:
    """Управляет процессом Remote — по образцу ImagineProcess (см. её
    докстринг про перехват stdout/stderr вместо DEVNULL)."""

    def __init__(self, host, port, comfy_host, comfy_port, imagine_port=None, dev_mode=False):
        self.host = host
        self.port = port
        self.comfy_host = comfy_host
        self.comfy_port = comfy_port
        self.imagine_port = imagine_port
        self.dev_mode = dev_mode
        self.proc = None

    def start(self):
        cmd, cwd, error = resolve_remote_launch(
            self.host, self.port, self.comfy_host, self.comfy_port,
            self.imagine_port, self.dev_mode,
        )
        if error:
            raise RuntimeError(error)

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        log.info(
            "Запуск Remote: %s (cwd=%s, comfyui=%s:%s, imagine_port=%s)",
            cmd, cwd, self.comfy_host, self.comfy_port, self.imagine_port,
        )
        self.proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            creationflags=creationflags,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        watcher = threading.Thread(target=self._log_exit, daemon=True)
        watcher.start()
        return self.proc

    def _log_exit(self):
        proc = self.proc
        if proc is None:
            return
        output = ""
        try:
            output = proc.stdout.read() if proc.stdout else ""
        except Exception:
            log.exception("Не удалось прочитать вывод процесса Remote")
        finally:
            if proc.stdout:
                try:
                    proc.stdout.close()
                except Exception:
                    pass
        code = proc.wait()
        if code == 0:
            log.info("Remote (PID %s) завершился, код выхода 0", proc.pid)
        else:
            tail = output[-4000:] if output else "(процесс не вывел ничего в stdout/stderr)"
            log.error(
                "Remote (PID %s) завершился с кодом %s. Вывод процесса:\n%s",
                proc.pid, code, tail,
            )

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def exit_code(self):
        return self.proc.returncode if self.proc is not None else None

    def stop(self):
        if self.proc is None:
            return
        pid = self.proc.pid
        log.info("Остановка Remote (PID %s)", pid)
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                log.exception("Не удалось выполнить taskkill для PID %s", pid)
        else:
            try:
                self.proc.terminate()
            except Exception:
                log.exception("Не удалось остановить процесс PID %s", pid)
        try:
            self.proc.wait(timeout=5)
        except Exception:
            pass
        self.proc = None


def is_remote_available(port, timeout=1.0):
    """Простой опрос готовности -- аналог is_imagine_available(). GET
    /status требует Bearer-токен (см. remote/auth.py) и без него
    честно отвечает 401, а не 200 -- но нам для проверки "поднялся ли
    процесс вообще" достаточно ЛЮБОГО HTTP-ответа сервера (в т.ч. 401),
    в отличие от ConnectionError/timeout ("порт ещё не слушает")."""
    url = f"http://127.0.0.1:{port}/api/v1/remote/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


def call_local_api(port, method, path, json_body=None, timeout=3.0):
    """Небольшой хелпер для вызовов Remote API с локального ПК из Studio
    UI (получение pairing-кода, список устройств, revoke) -- Settings UI
    работает в отдельном процессе Qt, поэтому обращается к Remote так же,
    как это в будущем будет делать сам телефон, просто без Bearer-токена
    (эти конкретные эндпоинты защищены проверкой адреса клиента, см.
    remote/local_guard.py, а не токеном устройства).

    Бросает urllib.error.HTTPError (со статус-кодом и телом ответа,
    JSON {"detail": "..."} от FastAPI) при 4xx/5xx и urllib.error.URLError
    при недоступности процесса -- вызывающая сторона (ui/settings/
    remote_page.py через launcher_window.py) сама решает, как это
    показать пользователю."""
    url = f"http://127.0.0.1:{port}{path}"
    data = _json.dumps(json_body).encode("utf-8") if json_body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return _json.loads(body) if body else None


def extract_error_detail(http_error: urllib.error.HTTPError) -> str:
    """Достаёт человекочитаемое сообщение из тела ответа FastAPI-ошибки
    ({"detail": "..."}) -- иначе пользователь видит только "HTTP Error
    400: Bad Request" без объяснения причины (неверный код, код истёк,
    и т.п. -- см. remote/pairing.py PairingError)."""
    try:
        body = http_error.read()
        data = _json.loads(body) if body else {}
        detail = data.get("detail") if isinstance(data, dict) else None
        return detail or str(http_error)
    except Exception:
        return str(http_error)


def get_lan_ip():
    """Лучшее предположение о LAN-адресе этого ПК -- для показа
    пользователю в ui/settings/remote_page.py ("адрес для телефона"),
    когда включён доступ по локальной сети (см. host="0.0.0.0" там же).
    Не для чего-либо, влияющего на реальную маршрутизацию: сам Remote
    слушает 0.0.0.0 (все интерфейсы) независимо от того, что вернёт эта
    функция -- это только подсказка человеку, какой IP набирать на
    телефоне.

    Трюк с UDP-сокетом на 8.8.8.8:80 -- ничего никуда не отправляет
    (UDP connect() не делает handshake, только выбирает исходящий
    интерфейс ОС), просто спрашивает ОС, каким локальным адресом она бы
    воспользовалась для маршрута к произвольному внешнему адресу -- тот
    же приём, что повсеместно используется для этой задачи в Python
    (нет кросс-платформенного способа спросить "мой LAN IP" напрямую).
    Возвращает None, если определить не удалось (нет сети вовсе, и
    т.п.) -- тогда remote_page.py просто не показывает подсказку."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
