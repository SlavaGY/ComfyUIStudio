"""
Сетевые хелперы Remote, не связанные с жизненным циклом его процесса
(этап R3: вынесено из remote_process.py).

  * is_remote_available() — опрос готовности подпроцесса Remote;
  * call_local_api() / extract_error_detail() — вызовы Remote API с
    локального ПК из Studio UI (pairing, список устройств, отзыв);
  * get_lan_ip() — подсказка «какой IP набирать на телефоне».

Модуль не импортирует Qt и не импортирует remote_process.py: его
используют и launcher_window.py, и сам процесс Remote
(remote/mdns.py -> get_lan_ip).
"""

from __future__ import annotations

import json as _json
import urllib.error
import urllib.request


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
