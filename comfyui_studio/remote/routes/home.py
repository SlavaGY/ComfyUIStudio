"""
GET `/` и GET `/remote/home` -- домашняя страница "терминала" (этап 4
дорожной карты, §0.3 "модель терминала"): список "апп" из
`apps_registry.py`, отдаётся самим процессом Remote (не Imagine) -- по
сути, "рабочий стол" телефона.

Простая серверная HTML-строка вместо шаблонизатора (Jinja2 и т.п.) --
страница из одного экрана с несколькими плитками не оправдывает новую
зависимость только ради неё; если апп станет больше и разметка
усложнится, тогда и стоит заводить настоящие шаблоны.

И эта страница, И reverse-proxy к Imagine (`imagine_proxy.py`)
защищены одной и той же проверкой `proxy_auth.resolve_device_and_token`
(не `auth.require_device`, как у остального REST API этого пакета) --
см. её докстринг про то, почему обычному браузеру (а не только
Android) нужны ещё query-параметр и cookie в дополнение к заголовку
Authorization.

НОВОЕ (этап "Запуск приложений через Remote", между этапами 6 и 6.5
дорожной карты): раньше плитка появлялась только для уже запущенных
приложений (см. историю list_available_apps в apps_registry.py) -- с
телефона нельзя было даже увидеть Imagine, пока его не запускали
вручную через Studio. Теперь показываются ВСЕ зарегистрированные
приложения; для незапущенных и умеющих стартовать через Remote (см.
RemoteApp.start_fn) -- кнопка "Запустить" вместо ссылки, дергающая
`POST /apps/{id}/start` (см. routes/apps.py) через
`window.__REMOTE_TOKEN__` в заголовке Authorization (обычный fetch()
умеет ставить заголовки -- в отличие от WebSocket-хендшейка, см.
proxy_auth.py, ограничение здесь не действует) и затем опрашивающая
`GET /apps` каждые несколько секунд, пока статус не станет "running".
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..apps_registry import RemoteApp, list_all_apps
from ..proxy_auth import resolve_device_and_token, set_token_cookie

router = APIRouter()

_UNAUTHORIZED_HTML = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ComfyUI Studio</title></head>
<body style="font-family:sans-serif; padding:2rem;">
<h1>Нужен токен</h1>
<p>Откройте эту ссылку с <code>?token=...</code> -- токен выдаётся при
сопряжении телефона в разделе «Удалённый доступ» настроек Studio.</p>
</body></html>"""


def _render_tile(app: RemoteApp) -> str:
    status = app.status_fn() if app.status_fn else "stopped"
    if status == "running":
        return f'<a class="tile" href="{app.path}">{app.name}</a>\n'
    if status == "starting":
        return (
            f'<div class="tile tile-pending" data-app-id="{app.id}" data-app-status="starting">'
            f'{app.name} <span class="hint">запускается…</span></div>\n'
        )
    if app.start_fn is not None:
        # НОВОЕ (автозапуск ComfyUI): если предыдущая попытка запуска
        # оборвалась асинхронной ошибкой (ComfyUI не поднялся вовремя,
        # упал сразу после старта и т.п. -- см. app_launcher.imagine_last_error()),
        # статус к этому моменту уже вернулся к "stopped" (см.
        # pollUntilRunning ниже -- перезагружает страницу и на "running",
        # И на "stopped"), но сама ошибка ещё жива -- показываем её под
        # кнопкой, а не молча предлагаем нажать "Запустить" заново без
        # объяснения, что пошло не так в прошлый раз.
        error = app.error_fn() if app.error_fn else None
        error_html = f'<div class="tile-error">{error}</div>' if error else ""
        return (
            f'<button class="tile tile-startable" data-app-id="{app.id}" '
            f'onclick="startApp(this)">{app.name} <span class="hint">запустить</span></button>\n'
            f'{error_html}\n'
        )
    # Зарегистрировано только для отображения статуса -- запускать
    # с телефона пока нечем (см. RemoteApp.start_fn), и оно сейчас не
    # запущено -- честно показываем как недоступное, без действия.
    return f'<div class="tile tile-disabled">{app.name} <span class="hint">недоступно</span></div>\n'


def _render_home_html(apps: list[RemoteApp], token: str) -> str:
    if apps:
        tiles = "".join(_render_tile(app) for app in apps)
    else:
        tiles = '<p class="empty">Нет зарегистрированных приложений.</p>'

    # window.__REMOTE_TOKEN__ -- см. §0.3, вариант 2 (используется и
    # проксируемым JS-фронтендом Imagine для WS, и теперь этой же
    # страницей для запроса запуска приложений, см. startApp() ниже).
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ComfyUI Studio</title>
<style>
  body {{ font-family: sans-serif; background:#111; color:#eee; margin:0; padding:2rem; }}
  h1 {{ font-size:1.3rem; font-weight:600; }}
  .tile {{ display:block; width:100%; box-sizing:border-box; text-align:left;
           padding:1.25rem; margin-bottom:1rem; background:#222; border:none;
           border-radius:.5rem; color:#fff; text-decoration:none; font-size:1.1rem;
           font-family:inherit; }}
  .tile:active {{ background:#333; }}
  .tile-startable {{ cursor:pointer; }}
  .tile-pending, .tile-disabled {{ color:#aaa; }}
  .tile-error {{ color:#e5484d; font-size:.85rem; margin:-.75rem 0 1rem; padding:0 .25rem; }}
  .hint {{ color:#999; font-size:.85rem; }}
  .empty {{ color:#999; }}
</style>
<script>window.__REMOTE_TOKEN__ = {token!r};</script>
</head>
<body>
<h1>ComfyUI Studio</h1>
<div id="tiles">
{tiles}
</div>
<script>
// Кнопка "Запустить" -- POST /apps/{{id}}/start с Bearer-заголовком
// (обычный fetch умеет заголовки, в отличие от WS-хендшейка), затем
// опрос GET /apps раз в 2с, пока статус не станет "running" -- тогда
// просто перезагружаем страницу, сервер сам отрисует уже ссылку.
//
// НОВОЕ: при неуспехе (напр. "ComfyUI не запущен" из app_launcher.py)
// показываем ИМЕННО текст detail от сервера, а не общее "ошибка" --
// без этого сообщение вида "откройте ComfyUI Studio на ПК и нажмите
// Старт" до пользователя просто не доходило (см. живой баг: Imagine
// открывался, но не работал, а телефон не объяснял почему).
async function startApp(button) {{
    const appId = button.dataset.appId;
    button.disabled = true;
    button.querySelector('.hint').textContent = 'запускается…';
    try {{
        const resp = await fetch('/api/v1/remote/apps/' + appId + '/start', {{
            method: 'POST',
            headers: {{ 'Authorization': 'Bearer ' + window.__REMOTE_TOKEN__ }},
        }});
        if (!resp.ok) {{
            let detail = 'сервер отклонил запуск (' + resp.status + ')';
            try {{
                const body = await resp.json();
                if (body && body.detail) {{ detail = body.detail; }}
            }} catch (e) {{
                // тело не JSON -- оставляем общий текст выше
            }}
            button.disabled = false;
            button.querySelector('.hint').textContent = detail;
            return;
        }}
    }} catch (e) {{
        button.disabled = false;
        button.querySelector('.hint').textContent = 'нет соединения, повторите';
        return;
    }}
    pollUntilRunning(appId);
}}

async function pollUntilRunning(appId) {{
    try {{
        const resp = await fetch('/api/v1/remote/apps', {{
            headers: {{ 'Authorization': 'Bearer ' + window.__REMOTE_TOKEN__ }},
        }});
        const apps = await resp.json();
        const app = apps.find(a => a.id === appId);
        // "running" -- готово; "stopped" -- цепочка запуска оборвалась
        // асинхронной ошибкой (ComfyUI не поднялся и т.п., см.
        // app_launcher.imagine_last_error()) -- в обоих случаях просто
        // перезагружаем: сервер сам отрисует либо ссылку, либо кнопку
        // "Запустить" с текстом ошибки под ней (см. _render_tile выше).
        // Продолжаем опрос только пока статус остаётся "starting".
        if (app && app.status !== 'starting') {{
            window.location.reload();
            return;
        }}
    }} catch (e) {{
        // сеть моргнула -- просто попробуем ещё раз следующим тиком
    }}
    setTimeout(() => pollUntilRunning(appId), 2000);
}}

// Плитки уже в состоянии "starting" при загрузке страницы (например,
// открыли терминал сразу после нажатия "Запустить") -- тоже начинаем
// опрос сами, не дожидаясь клика.
document.querySelectorAll('.tile-pending').forEach(el => {{
    pollUntilRunning(el.dataset.appId);
}});
</script>
</body>
</html>"""


@router.get("/", response_class=HTMLResponse)
@router.get("/remote/home", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    resolved = resolve_device_and_token(request)
    if resolved is None:
        return HTMLResponse(_UNAUTHORIZED_HTML, status_code=401)

    _device_id, token = resolved
    # asyncio.to_thread -- та же причина, что и в imagine_proxy.py:
    # _render_home_html() внутри дёргает app.status_fn() для каждой
    # плитки, а тот (см. app_launcher.imagine_status()) синхронно
    # блокирующий (urlopen с таймаутом до 1с). Здесь это не так
    # критично, как было в proxy (эта страница отдаётся один раз на
    # заход в терминал, а не на каждую картинку), но тот же паттерн
    # "блокирующий вызов внутри async def" одинаково не к месту что
    # там, что тут -- не блокировать event loop Remote целиком, пока
    # непонятно, запущен ли Imagine.
    html = await asyncio.to_thread(_render_home_html, list_all_apps(), token)
    response = HTMLResponse(html)
    if request.query_params.get("token"):
        # Токен пришёл в URL -- закрепляем его в куке (см.
        # proxy_auth.py), чтобы дальнейшая навигация (клик по плитке,
        # подгрузка статики проксируемого апп) обычным браузером не
        # требовала повторно дописывать ?token= в каждый URL вручную.
        set_token_cookie(response, token)
    return response
