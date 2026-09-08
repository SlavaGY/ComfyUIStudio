"""
comfyui_studio.remote — REST API для удалённого/мобильного доступа к
ComfyUI Studio (см. ComfyUIStudio_Remote_Roadmap.md в корне репозитория).

Архитектурная развилка §0.1 дорожной карты решена в пользу отдельного
пакета, работающего как СВОЙ ПРОЦЕСС — по тому же образцу, что уже
устроен comfyui_studio/imagine/ (свой FastAPI/uvicorn-процесс,
диспетчеризуемый из корневого main.py скрытым CLI-флагом, см.
REMOTE_CLI_FLAG в __main__.py и launcher/core/remote_process.py).
Remote — не довесок к Imagine, а универсальный шлюз/терминал-сервер:
Imagine — лишь одно из "апп", доступных через него (см. §0.1, apps_registry
появится на этапе 4).

Этап 1 ("Remote API Core", уже реализован — см. README и роадмап):
pairing (одноразовый код -> постоянный токен устройства), хранилище
устройств, базовые статус/очередь-эндпоинты. Этап 2 ("Realtime (грубый
статус) + WS-инфраструктура", тоже уже реализован): WS-эндпоинт
`/api/v1/remote/ws?token=...`, события queue.update/generation.started/
generation.completed/generation.error, фоновый опрос ComfyUI+Imagine
(см. generation_watcher.py). Этап 3 ("Пошаговый прогресс", тоже уже
реализован): события generation.progress -- источник не сам Remote, а
comfyui_studio/imagine/backend/progress_forwarder.py (см. его докстринг
про то, почему WS-клиент для прогресса обязан жить именно в процессе
Imagine), пересылающий их сюда через routes/internal.py. Этап 4
("Реестр апп + reverse-proxy на сервере", тоже уже реализован):
динамический реестр apps_registry.py, домашняя страница терминала
(routes/home.py, GET `/` и `/remote/home`) и reverse-proxy к Imagine
(imagine_proxy.py, `/apps/imagine/*`), с внедрением токена в HTML-
страницу Imagine (см. §0.3 дорожной карты про модель "терминала").
Все этапы 1–4 подтверждены живым тестированием, включая доступ по
локальной сети с реального телефона (см. README, "Известные
ограничения"). Этап 5 ("mDNS discovery", тоже уже реализован):
объявление Remote в локальной сети через `zeroconf` (mdns.py), чтобы
телефону не нужно было вводить IP этого ПК вручную. Ещё нет:
Android-стороны (этапы 6+).

Структура пакета (см. отдельные модули за докстрингами):
    models.py       — pydantic DTO (RemoteStatus, PairingCodeResponse, ...)
    device_store.py — JSON-хранилище устройств (%APPDATA%\\ComfyUIStudio\\
                       remote_devices.json), хранит только hash(token)
    pairing.py       — одноразовый pairing-код -> токен устройства
    auth.py          — FastAPI-зависимость проверки Bearer-токена (HTTP)
                       и её WS-вариант (токен query-параметром)
    proxy_auth.py     — проверка токена для "терминальных" маршрутов
                       (домашняя страница + reverse-proxy), см. §0.3 —
                       заголовок, кука или query-параметр, в отличие от
                       auth.py (только заголовок)
    local_guard.py    — проверка "вызов только с самого ПК" (127.0.0.1)
                       для эндпоинтов, управляемых из Studio UI/других
                       Studio-процессов (Imagine, см. routes/internal.py)
    apps_registry.py  — динамический реестр приложений терминала (этап 4)
    imagine_proxy.py   — reverse-proxy `/apps/imagine/*` -> Imagine (этап 4)
    mdns.py            — mDNS-объявление Remote в локальной сети (этап 5)
    state.py          — параметры запуска процесса (comfy/imagine порты,
                       и с этапа 5 -- собственные host/port Remote)
    gpu_stats.py       — автономный (без Qt) опрос GPU через pynvml
    ws_hub.py           — реестр активных WS-подключений (этап 2)
    generation_watcher.py -- фоновый опрос очереди ComfyUI/статуса
                       генераций Imagine, источник событий для ws_hub
                       (этап 2)
    app.py             — сборка FastAPI-приложения из routes/ + lifespan
                       (запуск GenerationWatcher, регистрация Imagine в
                       apps_registry, запуск/остановка mDNS)
    __main__.py         — точка входа `python -m comfyui_studio.remote`
    routes/            — по одному модулю на группу эндпоинтов, включая
                       internal.py (этап 3 — приём от Imagine) и
                       home.py (этап 4 — домашняя страница терминала)
"""
