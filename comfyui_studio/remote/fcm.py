"""
Отправка push-уведомлений через Firebase Cloud Messaging (FCM HTTP v1
API) -- §Этап 6.5 дорожной карты ("[Решено -- используем FCM]":
бесплатно без ограничений на личных устройствах).

Единственный триггер -- generation.completed/generation.error из
generation_watcher.py (см. её докстринг про источник этих событий) --
push дублирует то же самое WS-событие для случая, когда WebView не
выполняется (свёрнутое приложение/выключенный экран, см. §Этап 6.5:
"это единственная функция, которая по своей природе не укладывается в
модель чистого терминала").

Путь к JSON-файлу сервис-аккаунта -- `cfg["remote"]["fcm_service_account_path"]`
в config.json Studio (тот же Qt-независимый `launcher.core.config.
load_config()`, что уже используют comfy_launcher.py/app_launcher.py,
см. их докстринги про то, почему это безопасно для Qt-независимого
процесса Remote) -- НЕ переменная окружения и не хардкод, чтобы путь
настраивался в UI Studio (см. новое поле в ui/settings/remote_page.py).

Если сервис-аккаунт не настроен вовсе -- push тихо не отправляется:
это опциональная функция, отсутствие Firebase-проекта не должно ронять
сам Remote или как-либо блокировать основную WS-доставку (§2), которая
работает независимо от push и всегда была и остаётся основным каналом.

Зависимость `google-auth[requests]` (см. pyproject.toml — extras `[requests]`
обязательны, сам `google-auth` не тянет `requests` как обязательную
зависимость, хотя `google.auth.transport.requests.Request` от него
требует) -- только для OAuth2 к Google (`service_account.Credentials`/
`Request` для обновления access-токена); сама отправка сообщения --
обычный `urllib`, без дополнительных Google SDK (`google-cloud-messaging`
тянет за собой значительно больше, чем нужно для одного HTTP POST).
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Optional

from ..launcher.core.config import load_config
from ..launcher.core.logging_setup import log
from .device_store import list_fcm_tokens

FCM_SEND_TIMEOUT_SECONDS = 5.0


def _peek_json(path: str) -> Optional[dict]:
    """Читает файл заново, независимо от того, что произошло при
    попытке загрузить его как сервис-аккаунт -- используется только для
    диагностики (см. вызывающий код: различить "это вообще не JSON /
    файла нет" от "это валидный JSON, но не того типа файла"). Никогда
    не бросает исключений -- это вспомогательная проверка для текста
    ошибки, а не часть основной логики."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None

_lock = threading.Lock()
_cached_credentials = None
_cached_project_id: Optional[str] = None
_cached_path: Optional[str] = None


def _load_credentials() -> tuple[Optional[str], Optional[str]]:
    """Возвращает (access_token, project_id) или (None, None), если
    сервис-аккаунт не настроен/не читается/`google-auth` не установлен --
    во всех этих случаях вызывающая сторона просто пропускает отправку
    (см. докстринг модуля)."""
    global _cached_credentials, _cached_project_id, _cached_path

    path = load_config().get("remote", {}).get("fcm_service_account_path")
    if not path:
        return None, None

    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as e:
        # google-auth -- часть стандартных зависимостей проекта (см.
        # pyproject.toml), но не у всех текущих пользователей она уже
        # установлена (обновили код, ещё не переустановили окружение) --
        # это не должно ронять Remote, только тихо отключать push.
        #
        # ВАЖНО: этот except ловит ЛЮБОЙ ImportError из цепочки импортов
        # выше -- не только "google-auth не установлен вовсе", но и сбой
        # ЛЮБОЙ его транзитивной зависимости (cryptography, pyasn1,
        # cffi...), например несовместимую версию/битый нативный модуль.
        # Раньше здесь логировалось одно и то же сообщение независимо от
        # реальной причины -- живой случай (2026-09-08): google-auth
        # был установлен именно в тот venv, которым запускается Remote,
        # но предупреждение всё равно появлялось -- без текста самого
        # исключения продиагностировать это было невозможно. Теперь
        # str(e) всегда попадает в лог.
        log.warning("Не удалось импортировать google-auth -- push-уведомления недоступны: %s", e)
        return None, None

    with _lock:
        if _cached_credentials is None or path != _cached_path:
            try:
                _cached_credentials = service_account.Credentials.from_service_account_file(
                    path, scopes=["https://www.googleapis.com/auth/firebase.messaging"]
                )
                with open(path, "r", encoding="utf-8") as f:
                    _cached_project_id = json.load(f).get("project_id")
                _cached_path = path
            except (OSError, ValueError, KeyError) as e:
                # ЖИВОЙ СЛУЧАЙ (2026-09-08): пользователь указал сюда
                # android/app/google-services.json (конфиг Android-
                # приложения — project_info/client/api_key) вместо JSON
                # сервисного аккаунта (client_email/private_key/
                # token_uri) — два совершенно разных файла с одного и
                # того же Firebase-проекта, которые легко перепутать.
                # _peek_json() читает файл заново и независимо от того,
                # на каком шаге выше сорвалось исключение (Credentials.
                # from_service_account_file могла упасть до нашего
                # собственного open() ниже) — раз файл в принципе
                # существует и это валидный JSON, можно проверить его
                # форму и дать точную подсказку вместо общей ошибки.
                probe = _peek_json(path)
                if isinstance(probe, dict) and "project_info" in probe:
                    log.error(
                        "В настройках push указан android/app/google-services.json "
                        "(конфиг Android-приложения) — нужен другой файл: JSON "
                        "сервисного аккаунта из Firebase Console -> Настройки проекта -> "
                        "Service Accounts -> Generate new private key. (%s)",
                        path,
                    )
                else:
                    log.error("Не удалось прочитать сервис-аккаунт FCM (%s): %s", path, e)
                _cached_credentials = None
                return None, None

        if not _cached_credentials.valid:
            try:
                _cached_credentials.refresh(Request())
            except Exception as e:
                log.error("Не удалось обновить токен доступа Google для FCM: %s", e)
                return None, None

        return _cached_credentials.token, _cached_project_id


def _format_notification(event: dict) -> tuple[Optional[str], Optional[str]]:
    """(title, body) для push, или (None, None), если это событие не
    предназначено для push (см. вызывающий код -- сейчас реагируем
    только на generation.completed/generation.error, см. §Этап 6.5)."""
    event_type = event.get("type")
    if event_type == "generation.completed":
        n = len(event.get("image_urls") or [])
        if n:
            return "Готово", f"Сгенерировано изображений: {n}"
        return "Готово", "Генерация завершена"
    if event_type == "generation.error":
        return "Ошибка генерации", str(event.get("message", "")) or "Подробности в логе ComfyUI."
    return None, None


def send_generation_push(event: dict) -> None:
    """Синхронная функция (см. вызов через asyncio.to_thread в
    generation_watcher.py -- тот же приём, что и у ComfyAPIClient там
    же, чтобы обычный блокирующий urllib не стопорил event loop
    Remote). Никогда не бросает исключений наружу -- push вторичен по
    отношению к WS-доставке (§Этап 6.5), сбой отправки не должен
    как-либо влиять на остальную работу Remote."""
    try:
        _send_generation_push_unsafe(event)
    except Exception:
        log.exception("Непредвиденная ошибка при отправке push-уведомления")


def _send_generation_push_unsafe(event: dict) -> None:
    tokens = list_fcm_tokens()
    if not tokens:
        return

    title, body = _format_notification(event)
    if title is None:
        return

    access_token, project_id = _load_credentials()
    if not access_token:
        return

    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    for token in tokens:
        message = {
            "message": {
                "token": token,
                # НАМЕРЕННО только "data", без "notification" -- при обоих
                # блоках сразу Android показывает уведомление СИСТЕМОЙ
                # напрямую (в обход FirebaseMessagingService.onMessageReceived
                # на Android), когда приложение свёрнуто/убито -- то есть
                # ровно в том случае, ради которого push вообще нужен (§Этап
                # 6.5: "пока приложение свёрнуто... WS-событие никто не
                # увидит"). Системное уведомление в этом случае вело бы по
                # тапу просто в приложение по умолчанию, без перехода сразу
                # на нужный апп (см. FcmService.kt::onMessageReceived и
                # TerminalActivity.EXTRA_OPEN_PATH) -- только "data" гарантирует,
                # что onMessageReceived вызывается ВСЕГДА, независимо от
                # состояния приложения, и мы сами строим уведомление с
                # нужным PendingIntent в любом случае.
                "data": {
                    "title": title,
                    "body": body,
                    "prompt_id": str(event.get("prompt_id", "")),
                    # НОВОЕ (автосохранение картинок на телефон, см.
                    # GeneratedImageSaver.kt): исходный "type" события
                    # ("generation.completed"/"generation.error") --
                    # раньше клиент мог различить их только по ТЕКСТУ
                    # заголовка ("Готово" vs "Ошибка генерации"), что
                    # хрупко (текст мог когда-нибудь измениться/
                    # локализоваться) -- теперь FcmService.kt сам решает,
                    # стоит ли идти скачивать картинки, по этому полю, а
                    # не парся title.
                    "state": str(event.get("type", "")),
                },
                "android": {"priority": "high"},
            }
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(message).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=FCM_SEND_TIMEOUT_SECONDS)
        except urllib.error.HTTPError as e:
            # 404/400 обычно значит "токен больше не валиден"
            # (переустановка приложения, сброс данных телефона и т.п.) --
            # не вычищаем его здесь же из device_store (это сделает
            # следующее обновление токена через POST /fcm-token, если
            # приложение ещё живо, или ручной revoke из Studio, если
            # нет) -- просто логируем и продолжаем со следующим токеном.
            log.warning("FCM отклонил push (токен %s…): %s", token[:12], e)
        except (urllib.error.URLError, OSError) as e:
            log.warning("Не удалось отправить push через FCM: %s", e)
