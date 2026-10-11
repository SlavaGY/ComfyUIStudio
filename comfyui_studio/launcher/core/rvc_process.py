"""
Запуск и остановка Ultimate RVC (https://github.com/JackismyShephard/ultimate-rvc)
как ДОЧЕРНЕГО ПРОЦЕССА лаунчера -- этап RVC-1.

Идея та же, что у ComfyUI: Studio не тащит RVC в свой процесс (у RVC свой
Python 3.13 + torch/CUDA в собственном venv, смешивать их с окружением
Studio нельзя), а запускает его родным способом и показывает его
веб-интерфейс (Gradio) во встроенном браузере. Это ЭТАП 1. Дальше (этап
RVC-2, см. docs/RVC_APPLY.md) поверх того же процесса/настроек появится
собственный интерфейс Studio -- по образцу Imagine поверх ComfyUI:
отдельный лёгкий сервис, который запускается ПИТОНОМ ИЗ venv RVC и зовёт
`ultimate_rvc.core` напрямую (fastapi/uvicorn в venv RVC уже есть), а
Studio общается с ним по HTTP. Поэтому уже сейчас:

  * вся «где лежит RVC и чем его запускать» -- здесь, в одном месте
    (resolve_rvc_launch), и переиспользуется любым будущим режимом;
  * настройки лежат в cfg["rvc"] отдельной секцией (rvc_settings), без
    привязки к ComfyUI/Imagine -- RVC не зависит от ComfyUI.

Модуль намеренно НЕ импортирует Qt (как managed_process.py): им смогут
пользоваться и Remote, и тесты без PySide6.

Что предполагается про установку (проверено по листингу папки
D:\\AI\\ultimate-rvc, а не по исходникам -- см. «Что НЕ проверено» в
docs/RVC_APPLY.md): venv лежит в `uv\\.venv` (так его создаёт urvc.ps1),
в нём есть консольный скрипт `urvc-web.exe` (точка входа веб-интерфейса).
Порт передаётся переменной окружения GRADIO_SERVER_PORT (стандартный
механизм Gradio, не зависит от флагов urvc-web); если у вашей версии
urvc-web есть свой флаг порта, он, будучи указан в «Доп. аргументах»,
имеет приоритет.
"""

from __future__ import annotations

import os
import shlex
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections import deque

from ...errors import ProcessStartError
from .constants import PROJECT_ROOT
from .logging_setup import log
from .managed_process import EXIT_TAIL_CHARS, ManagedProcess

# 7860 -- Imagine, 7861 -- Remote, 7862 -- Imagine Pony (см. DEFAULT_CONFIG
# в constants.py). Gradio по умолчанию тоже садится на 7860, поэтому RVC
# получает свой порт -- иначе он конфликтовал бы с Imagine.
DEFAULT_RVC_PORT = 7863

RVC_DEFAULTS = {
    "root_path": "",
    "port": DEFAULT_RVC_PORT,
    # Свободная строка доп. аргументов для urvc-web (разбирается как
    # командная строка: кавычки для путей с пробелами работают).
    "launch_args": "",
    # Поднимать RVC сразу при старте Studio.
    "auto_start": False,
}

# Где искать venv относительно корня установки. Первым -- `uv\.venv`
# (раскладка urvc.ps1, как у владельца проекта), дальше -- обычные имена.
VENV_CANDIDATES = ("uv/.venv", ".venv", "venv", "uv/venv")

WEB_ENTRY_BASENAME = "urvc-web"

# Собственное приложение Studio для RVC (comfyui_studio/rvc/, этап RVC-2).
# Запускается ПИТОНОМ ИЗ venv RVC (там torch и ultimate_rvc), а сам пакет
# comfyui_studio подкладывается через PYTHONPATH -- в venv RVC он не
# установлен и устанавливаться не должен.
RVC_APP_MODULE = "comfyui_studio.rvc"
MODE_APP = "app"
MODE_GRADIO = "gradio"

# Лог-файл вывода RVC пишется потоком-читателем; чтобы он не рос бесконечно
# (tqdm/Gradio печатают много), при превышении файл начинается заново.
MAX_LOG_BYTES = 5 * 1024 * 1024
TAIL_LINES = 200


# --------------------------------------------------------------------------
# Настройки
# --------------------------------------------------------------------------


def rvc_settings(cfg):
    """Секция cfg["rvc"] поверх значений по умолчанию (старый config.json
    секции не содержит, а load_config() сливает только верхний уровень --
    вложенные словари значениями по умолчанию не дополняются)."""
    result = dict(RVC_DEFAULTS)
    section = (cfg or {}).get("rvc")
    if isinstance(section, dict):
        result.update(section)
    try:
        result["port"] = int(result["port"])
    except (TypeError, ValueError):
        result["port"] = DEFAULT_RVC_PORT
    result["root_path"] = str(result.get("root_path") or "").strip()
    result["launch_args"] = str(result.get("launch_args") or "").strip()
    result["auto_start"] = bool(result.get("auto_start"))
    return result


def port_conflict(cfg, port):
    """Название сервиса Studio, чей порт совпадает с `port`, либо None.
    Только настроенные в cfg порты -- реальную занятость порта проверяет
    is_port_in_use() при запуске."""
    cfg = cfg or {}
    taken = {
        "ComfyUI": cfg.get("port"),
        "Imagine": (cfg.get("imagine") or {}).get("port", 7860),
        "Remote": (cfg.get("remote") or {}).get("port", 7861),
        "Imagine Pony": (cfg.get("imagine_pony") or {}).get("port", 7862),
    }
    for name, value in taken.items():
        try:
            if value is not None and int(value) == int(port):
                return name
        except (TypeError, ValueError):
            continue
    return None


def parse_launch_args(text):
    """Строка доп. аргументов -> список. Кавычки поддерживаются; на
    Windows разбор не-POSIX (обратные слэши в путях остаются как есть),
    поэтому обрамляющие кавычки снимаются вручную."""
    text = (text or "").strip()
    if not text:
        return []
    posix = os.name != "nt"
    try:
        tokens = shlex.split(text, posix=posix)
    except ValueError:
        # Незакрытая кавычка и т.п. -- не роняем запуск, режем по пробелам.
        tokens = text.split()
    if not posix:
        tokens = [
            t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t
            for t in tokens
        ]
    return tokens


# --------------------------------------------------------------------------
# Поиск установки
# --------------------------------------------------------------------------


def _bin_dir(venv_dir):
    for name in ("Scripts", "bin"):
        path = os.path.join(venv_dir, name)
        if os.path.isdir(path):
            return path
    return None


def find_rvc_web_entry(root_path):
    """Путь к urvc-web(.exe) внутри venv установки либо None."""
    if not root_path or not os.path.isdir(root_path):
        return None
    for candidate in VENV_CANDIDATES:
        venv_dir = os.path.join(root_path, *candidate.split("/"))
        bin_dir = _bin_dir(venv_dir)
        if bin_dir is None:
            continue
        for name in (WEB_ENTRY_BASENAME + ".exe", WEB_ENTRY_BASENAME):
            path = os.path.join(bin_dir, name)
            if os.path.isfile(path):
                return path
    return None


def find_rvc_python(root_path):
    """python(.exe) из venv установки RVC либо None. Не нужен для этапа
    RVC-1 (запуск идёт через urvc-web), но это именно тот интерпретатор,
    которым этап RVC-2 запустит собственный сервис Studio."""
    if not root_path or not os.path.isdir(root_path):
        return None
    for candidate in VENV_CANDIDATES:
        venv_dir = os.path.join(root_path, *candidate.split("/"))
        bin_dir = _bin_dir(venv_dir)
        if bin_dir is None:
            continue
        for name in ("python.exe", "python"):
            path = os.path.join(bin_dir, name)
            if os.path.isfile(path):
                return path
    return None


def rvc_app_source_root():
    """Каталог, который кладётся в PYTHONPATH, чтобы питон RVC нашёл
    пакет comfyui_studio.rvc: корень исходников, а в собранном exe --
    папка с копией этого пакета в данных сборки (см. datas в *.spec:
    внутри exe пакеты лежат в архиве, питону другого процесса они
    недоступны)."""
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", ""), "rvc_app_src")
    return PROJECT_ROOT


def validate_rvc_root(root_path, mode=MODE_GRADIO):
    """(ok, сообщение) -- по аналогии с validate_portable_root() для
    ComfyUI. Сообщения на русском, в UI идут через _tr. Для приложения
    Studio нужен питон venv RVC, для оригинала -- urvc-web."""
    if not root_path or not os.path.isdir(root_path):
        return False, "Указанная папка не существует"
    if not os.path.isdir(os.path.join(root_path, "src", "ultimate_rvc")):
        return False, "Не найдена папка src\\ultimate_rvc — это не похоже на Ultimate RVC"
    if mode == MODE_APP:
        if find_rvc_python(root_path) is None:
            return False, (
                "Не найден python в виртуальном окружении (uv\\.venv\\Scripts). "
                "Установите Ultimate RVC (urvc.ps1 install)"
            )
        return True, "OK"
    if find_rvc_web_entry(root_path) is None:
        return False, (
            "Не найден urvc-web в виртуальном окружении (uv\\.venv\\Scripts). "
            "Установите Ultimate RVC (urvc.ps1 install)"
        )
    return True, "OK"


# --------------------------------------------------------------------------
# Команда запуска
# --------------------------------------------------------------------------


def build_rvc_env(settings, base_env=None, mode=MODE_GRADIO):
    """Окружение процесса RVC поверх `base_env` (по умолчанию os.environ).

    GRADIO_SERVER_NAME=127.0.0.1 -- веб-интерфейс слушает только этот ПК
    (доступ с телефона, когда понадобится, пойдёт через Remote, как у
    Imagine). PYTHONUTF8/PYTHONIOENCODING -- иначе вывод RVC в пайп на
    Windows идёт в cp1251, а в именах файлов/песен бывает кириллица и
    иероглифы."""
    env = dict(os.environ if base_env is None else base_env)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    if mode == MODE_APP:
        # Порт приложению Studio передаётся флагом --port (см. resolve_
        # rvc_launch), Gradio-переменные ему не нужны. PYTHONPATH --
        # чтобы питон venv RVC нашёл comfyui_studio.rvc; уже заданный
        # PYTHONPATH не затираем.
        source = rvc_app_source_root()
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = source + os.pathsep + existing if existing else source
        return env
    env.update(
        {
            "GRADIO_SERVER_NAME": "127.0.0.1",
            "GRADIO_SERVER_PORT": str(settings["port"]),
            "GRADIO_ANALYTICS_ENABLED": "False",
        }
    )
    return env


def resolve_rvc_launch(settings, base_env=None, mode=MODE_GRADIO, data_dir=None):
    """(cmd, cwd, env, error). При ошибке cmd/cwd/env == None, error --
    текст для пользователя (как resolve_imagine_launch()).

    mode='gradio' -- оригинальный urvc-web (порт через GRADIO_SERVER_PORT);
    mode='app'    -- приложение Studio `python -m comfyui_studio.rvc` питоном
                     venv RVC (порт флагом --port; data_dir -- где оно держит
                     загрузки, см. rvc/__main__.py)."""
    ok, message = validate_rvc_root(settings["root_path"], mode)
    if not ok:
        return None, None, None, message
    # cwd = корень установки: models/, audio/, temp/, logs/ у RVC лежат
    # именно там, так что запуск «из корня» -- как у родного urvc.ps1.
    cwd = settings["root_path"]
    if mode == MODE_APP:
        entry = os.path.join(rvc_app_source_root(), *RVC_APP_MODULE.split("."), "__main__.py")
        if not os.path.isfile(entry):
            return None, None, None, (
                f"Не найден пакет {RVC_APP_MODULE} ({entry}) — "
                "похоже, исходники комплекта повреждены или неполные."
            )
        cmd = [
            find_rvc_python(settings["root_path"]),
            "-m", RVC_APP_MODULE,
            "--host", "127.0.0.1",
            "--port", str(settings["port"]),
        ]
        if data_dir:
            cmd += ["--data-dir", data_dir]
        cmd += parse_launch_args(settings["launch_args"])
        return cmd, cwd, build_rvc_env(settings, base_env, mode), None
    entry = find_rvc_web_entry(settings["root_path"])
    cmd = [entry] + parse_launch_args(settings["launch_args"])
    return cmd, cwd, build_rvc_env(settings, base_env, mode), None


# --------------------------------------------------------------------------
# Сеть
# --------------------------------------------------------------------------


def is_port_in_use(port, host="127.0.0.1", timeout=0.5):
    """True, если на host:port уже кто-то слушает."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def is_rvc_available(port, timeout=1.0):
    """HTTP-опрос готовности: Gradio отдаёт страницу по '/', 200 достаточно."""
    url = f"http://127.0.0.1:{port}/"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except (urllib.error.URLError, OSError, ValueError):
        return False


# --------------------------------------------------------------------------
# Процесс
# --------------------------------------------------------------------------


class RvcProcess(ManagedProcess):
    """Ultimate RVC как дочерний процесс лаунчера.

    От ImagineProcess отличается выводом: RVC печатает много (tqdm,
    Gradio), поэтому ManagedProcess._spawn_captured() -- он читает ВЕСЬ
    stdout одним read() до завершения процесса -- не подходит: память
    росла бы всё время работы. Здесь вывод читается построчно потоком,
    пишется в лог-файл (с ограничением размера), а в памяти хранится
    только хвост -- он уходит в лог лаунчера, если процесс упал.
    """

    label = "Ultimate RVC"

    def __init__(self, settings, log_path=None, base_env=None, mode=MODE_GRADIO,
                 data_dir=None, on_line=None):
        """on_line(str) -- необязательный приёмник строк вывода (идёт из
        потока-читателя; тот, кто его передаёт, отвечает за потокобезопасность --
        RvcController отдаёт сигнал Qt)."""
        super().__init__()
        self.settings = dict(settings)
        self.port = self.settings["port"]
        self.log_path = log_path
        self.base_env = base_env
        self.mode = mode
        self.data_dir = data_dir
        self.on_line = on_line
        self.tail = deque(maxlen=TAIL_LINES)
        self._stopping = False

    def start(self):
        cmd, cwd, env, error = resolve_rvc_launch(
            self.settings, self.base_env, mode=self.mode, data_dir=self.data_dir
        )
        if error:
            raise ProcessStartError(error)
        if is_port_in_use(self.port):
            raise ProcessStartError(
                f"Порт {self.port} уже занят другой программой. "
                "Выберите другой порт в настройках Ultimate RVC."
            )
        log.info("Запуск Ultimate RVC: %s (cwd=%s, порт=%s)", cmd, cwd, self.port)
        self._stopping = False
        proc = self._spawn(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        threading.Thread(target=self._pump_output, args=(proc,), daemon=True).start()
        return proc

    def stop(self):
        # Флаг ставится ДО убийства: читатель вывода увидит ненулевой код
        # завершения и без него записал бы в лог «аварию» с хвостом вывода
        # на каждую обычную остановку по кнопке.
        self._stopping = True
        super().stop()

    def _pump_output(self, proc):
        sink = None
        written = 0
        try:
            if self.log_path:
                try:
                    os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
                    sink = open(self.log_path, "w", encoding="utf-8", errors="replace")
                except OSError:
                    log.exception("Не удалось открыть лог Ultimate RVC: %s", self.log_path)
                    sink = None
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                self.tail.append(line)
                if self.on_line is not None:
                    try:
                        self.on_line(line)
                    except (RuntimeError, TypeError, ValueError):
                        # Приёмник пропал (закрыто окно) или отказал --
                        # вывод процесса важнее, читаем дальше в файл.
                        self.on_line = None
                if sink is not None:
                    written += len(line) + 1
                    if written > MAX_LOG_BYTES:
                        sink.close()
                        sink = open(self.log_path, "w", encoding="utf-8", errors="replace")
                        sink.write("... лог усечён (превышен размер) ...\n")
                        written = 0
                    sink.write(line + "\n")
                    sink.flush()
        except (OSError, ValueError):
            log.exception("Ошибка чтения вывода %s", self.label)
        finally:
            if sink is not None:
                try:
                    sink.close()
                except OSError:
                    pass
            try:
                if proc.stdout:
                    proc.stdout.close()
            except (OSError, ValueError):
                pass
        code = proc.wait()
        if code == 0:
            log.info("%s (PID %s) завершился, код выхода 0", self.label, proc.pid)
        elif self._stopping:
            log.info("%s (PID %s) остановлен пользователем (код %s)", self.label, proc.pid, code)
        else:
            tail = "\n".join(self.tail)[-EXIT_TAIL_CHARS:] or (
                "(процесс не вывел ничего в stdout/stderr)"
            )
            log.error(
                "%s (PID %s) завершился с кодом %s. Хвост вывода:\n%s",
                self.label, proc.pid, code, tail,
            )


__all__ = [
    "MODE_APP",
    "MODE_GRADIO",
    "RVC_APP_MODULE",
    "rvc_app_source_root",
    "DEFAULT_RVC_PORT",
    "RVC_DEFAULTS",
    "RvcProcess",
    "build_rvc_env",
    "find_rvc_python",
    "find_rvc_web_entry",
    "is_port_in_use",
    "is_rvc_available",
    "parse_launch_args",
    "port_conflict",
    "resolve_rvc_launch",
    "rvc_settings",
    "validate_rvc_root",
]
