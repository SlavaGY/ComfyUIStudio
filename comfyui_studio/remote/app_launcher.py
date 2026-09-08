"""
Запуск зарегистрированных "апп" терминала по запросу с телефона --
этап дорожной карты, вставленный между этапом 6 (Android-терминал) и
этапом 6.5 (Push-уведомления): "Запуск приложений через Remote".

До этого этапа `apps_registry.py` только НАБЛЮДАЛ за Imagine
(`proxy_target()`/теперь `status_fn()` опрашивают, поднят ли он ПК-
стороной -- через Studio UI), но не мог сам его запустить: с телефона
приложение было не видно вообще, пока пользователь не нажимал
"Запустить" в самой Studio. Здесь -- недостающая половина: запуск
инициируется с телефона (`POST /apps/imagine/start`, см.
routes/apps.py), но сам механизм запуска Imagine НЕ дублируется --
целиком переиспользует уже существующий, Qt-независимый
`launcher.core.imagine_process.ImagineProcess`/`resolve_imagine_launch`
(тот же код, которым лаунчер пользуется для кнопки "Запустить" в своём
Qt UI). Это безопасно ровно потому, что тот модуль ни разу не
импортирует Qt (см. его собственный докстринг про сходство с
воркером эмбеддингов PromptVault) -- процесс Remote в принципе не
импортирует Qt ни при каких обстоятельствах (см. REMOTE_CLI_FLAG в
корневом main.py, перехватывается до `QApplication`).

Только Imagine на сегодняшний день -- единственное приложение в
реестре (см. apps_registry.py, app.py::_register_known_apps); если в
будущем добавится что-то ещё "запускаемое", по этому же образцу
заводится отдельная функция start_<app>()/<app>_status() и
регистрируется в app.py.

АВТОЗАПУСК ComfyUI (добавлено 2026-09-06, продолжение "живого бага" из
дорожной карты): раньше здесь была ТОЛЬКО проверка -- если ComfyUI не
поднят, Imagine отказывался стартовать с понятной ошибкой, но сам
ComfyUI не запускался, потому что казалось, что для этого пришлось бы
затащить в Remote Qt (`ComfyProcess` импортирует PySide6 на уровне
модуля -- мост живого лога в Qt-панель). При ближайшем рассмотрении
оказалось, что вся подготовка ЗАПУСКА (не логирования) ComfyUI --
`launcher.core.config.load_config/build_extra_launch_args/
prepare_launch_script` -- Qt-независима (простой JSON + текстовый .bat).
См. новый `comfy_launcher.py` -- Qt-свободный аналог `ComfyProcess`,
которым эта функция и пользуется, чтобы поднять ComfyUI САМА, а не
только проверить его наличие, и автоматически продолжить запуском
Imagine, как только ComfyUI станет доступен.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from ..launcher.core.imagine_process import ImagineProcess, is_imagine_available
from . import comfy_launcher
from .state import runtime

_lock = threading.Lock()
_process: Optional[ImagineProcess] = None
# True с момента, когда start_imagine() решил сначала поднять ComfyUI
# (см. _wait_for_comfy_then_start_imagine ниже), и до тех пор, пока
# либо не поднимется сам Imagine, либо цепочка не оборвётся по ошибке/
# таймауту -- imagine_status() показывает "starting" всё это время,
# терминал не видит разницы между "ждём ComfyUI" и "ждём сам Imagine"
# (с его точки зрения это один процесс запуска, см. routes/home.py).
_awaiting_comfy = False
# Сообщение о последней неудаче автозапуска (ComfyUI не поднялся за
# отведённое время, упал сразу после старта и т.п.) -- POST /start уже
# ответил 202 к моменту, когда это становится известно, единственный
# способ показать это на телефоне -- через GET /apps (см.
# imagine_last_error() и AppEntry.error в routes/apps.py).
_last_error: Optional[str] = None

# Сколько ждать, пока ComfyUI поднимется, прежде чем считать автозапуск
# неудавшимся -- ComfyUI загружает модели в память при старте, это
# может занимать заметное время (тем более на HDD/при первом запуске
# после перезагрузки, когда файлы ещё не в кэше ОС). Изначально было
# 180 секунд ("3 минуты с запасом больше, чем показывает LaunchWatcher
# для локального UI-запуска") -- живой тест (2026-09-08) показал
# холодный старт "больше пары минут", то есть опасно близко к прежнему
# порогу; увеличено до 300 секунд, чтобы холодный старт на медленном
# диске не обрывался ложным таймаутом прямо перед тем, как ComfyUI
# всё-таки поднимется (см. ComfyUIStudio_Remote_Roadmap.md, живой отчёт
# в этапе 6.5).
_COMFY_STARTUP_TIMEOUT_S = 300
_COMFY_POLL_INTERVAL_S = 2


def imagine_status() -> str:
    """"running" | "starting" | "stopped" -- см. докстринг
    RemoteApp.status_fn в apps_registry.py про то, зачем нужно именно
    три состояния, а не bool."""
    if runtime.imagine_port is None:
        # Studio вообще не передала --imagine-port этому запуску Remote
        # (см. state.py) -- значит, о самом существовании Imagine в
        # этой сессии ничего не известно, запускать нечего.
        return "stopped"
    if is_imagine_available(runtime.imagine_port):
        return "running"
    if _awaiting_comfy or (_process is not None and _process.is_running()):
        # Первое условие -- ждём, пока поднимется ComfyUI, ПЕРЕД тем как
        # даже начать запускать Imagine; второе -- сам подпроцесс Imagine
        # уже поднят, но ещё не отвечает на HTTP. Терминалу оба случая
        # показываются одинаково ("запускается…").
        return "starting"
    return "stopped"


def imagine_last_error() -> Optional[str]:
    """Последняя ошибка автозапуска -- либо явно записанная в
    _last_error (ComfyUI не поднялся за отведённое время, упал во время
    ожидания -- см. _wait_for_comfy_then_start_imagine), либо, если
    _last_error пуст, но подпроцесс Imagine сам завершился с ненулевым
    кодом СРАЗУ после того, как его удалось запустить (обнаружено живым
    тестом цепочки автозапуска: раньше в этом случае телефон видел
    просто "stopped" без единого объяснения, тот же класс проблемы, ради
    которого весь этот файл вообще существует -- см. докстринг модуля).
    Сбрасывается следующим успешным start_imagine() (через _last_error
    = None в начале, и через imagine_status() == "running" здесь, если
    новый подпроцесс поднялся)."""
    if _last_error:
        return _last_error
    if (
        _process is not None
        and not _process.is_running()
        and _process.exit_code() not in (None, 0)
        and imagine_status() == "stopped"
    ):
        return (
            f"Imagine завершился с кодом {_process.exit_code()} сразу после "
            "запуска -- проверьте лог Imagine на ПК (вкладка настроек в Studio)."
        )
    return None


def start_imagine() -> None:
    """Бросает RuntimeError только для СИНХРОННО обнаруживаемых ошибок
    (Imagine не настроен вовсе, конфигурация ComfyUI отсутствует -- см.
    comfy_launcher.start_comfyui()) -- вызывающая сторона (routes/apps.py)
    превращает это в HTTP 500 с тем же текстом. Ошибки, обнаруживаемые
    ПОЗЖЕ (ComfyUI не поднялся за отведённое время) не бросаются отсюда
    -- see imagine_last_error()/AppEntry.error."""
    global _process, _awaiting_comfy, _last_error
    if runtime.imagine_port is None:
        raise RuntimeError(
            "Imagine не настроен для этого запуска Remote -- перезапустите "
            "«Удалённый доступ» после включения Imagine в настройках Studio."
        )
    with _lock:
        # Повторный вызов, пока предыдущий запуск ещё идёт (ждём ComfyUI
        # ИЛИ ждём сам Imagine) или уже завершился успехом -- не плодит
        # вторую цепочку запуска (гонка возможна, если пользователь
        # нажмёт "Запустить" на телефоне несколько раз подряд).
        if imagine_status() in ("running", "starting"):
            return
        _last_error = None
        if comfy_launcher.is_comfyui_running(port=runtime.comfy_port):
            _spawn_imagine_process()
            return
        # ComfyUI не поднят -- поднимаем его сами и автоматически
        # продолжаем запуском Imagine, как только он станет доступен
        # (см. докстринг модуля про то, почему это теперь возможно).
        # port=runtime.comfy_port -- см. докстринг comfy_launcher.start_comfyui()
        # про то, почему это не должно молча падать на cfg["port"] по умолчанию.
        comfy_launcher.start_comfyui(port=runtime.comfy_port)  # может бросить RuntimeError -- пробрасываем как есть
        _awaiting_comfy = True
        threading.Thread(target=_wait_for_comfy_then_start_imagine, daemon=True).start()


def _spawn_imagine_process() -> None:
    """Вызывать только под _lock. Собственно запуск подпроцесса Imagine
    -- вынесено в отдельную функцию, т.к. вызывается из двух мест:
    напрямую (ComfyUI уже был поднят) и из фонового потока-ожидания
    ComfyUI (см. ниже)."""
    global _process
    proc = ImagineProcess(
        host="127.0.0.1",
        port=runtime.imagine_port,
        comfy_host=runtime.comfy_host,
        comfy_port=runtime.comfy_port,
        dev_mode=False,
        remote_port=runtime.port,
    )
    proc.start()  # может бросить RuntimeError -- см. вызывающий код ниже
    _process = proc


def _wait_for_comfy_then_start_imagine() -> None:
    global _awaiting_comfy, _last_error
    deadline = time.monotonic() + _COMFY_STARTUP_TIMEOUT_S
    try:
        while time.monotonic() < deadline:
            time.sleep(_COMFY_POLL_INTERVAL_S)
            if comfy_launcher.is_comfyui_running(port=runtime.comfy_port):
                with _lock:
                    _spawn_imagine_process()
                return
            comfy_error = comfy_launcher.last_error()
            if comfy_error:
                # ComfyUI сам упал во время запуска -- нет смысла ждать
                # дальше отведённый таймаут.
                with _lock:
                    _last_error = comfy_error
                return
        with _lock:
            _last_error = (
                f"ComfyUI не поднялся за {_COMFY_STARTUP_TIMEOUT_S} секунд -- "
                "проверьте лог запуска на ПК в Studio."
            )
    except Exception:
        # Не должно происходить (comfy_launcher/ImagineProcess сами не
        # бросают ничего непредвиденного), но фоновый поток без этого
        # упал бы молча, а телефон завис бы в "запускается…" навсегда.
        with _lock:
            _last_error = "Не удалось автоматически запустить Imagine после ComfyUI (см. лог Studio на ПК)."
        raise
    finally:
        with _lock:
            _awaiting_comfy = False


def clear_state() -> None:
    """Только для тестов -- модуль-уровневое состояние нужно сбрасывать
    между тестами, по тому же принципу, что apps_registry.clear_registry()."""
    global _process, _awaiting_comfy, _last_error
    _process = None
    _awaiting_comfy = False
    _last_error = None
