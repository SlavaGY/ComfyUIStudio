"""
mDNS-объявление Remote в локальной сети — этап 5 дорожной карты
(`ComfyUIStudio_Remote_Roadmap.md`, "mDNS discovery"): `zeroconf` на
сервере (этот модуль), будущий `NsdManager` на Android (этап 6+, ещё не
реализован) — по плану без изменений от версии 1 (§5).

Задача этого модуля исключительно в том, чтобы телефону в той же LAN
не приходилось вводить IP-адрес ПК вручную — увидел устройство в списке
("ComfyUI Studio (имя-пк)"), выбрал, дальше как обычно (pairing и т.д.,
см. этапы 1–4). Само обнаружение НИКАК не участвует в аутентификации —
это просто более удобный способ узнать host:port, к которому и так уже
можно подключиться напрямую, если адрес известен (см. §1.5/этап 4:
curl/браузер по IP работали и без mDNS).

Объявляется, ТОЛЬКО когда включён доступ по локальной сети (см.
`runtime.host == "0.0.0.0"`, чекбокс "Разрешить доступ по локальной
сети" в `ui/settings/remote_page.py`) — рекламировать сервис, слушающий
исключительно `127.0.0.1`, бессмысленно: до него всё равно не
достучаться ни по какому адресу, полученному через mDNS (см. тот же
принцип независимости от этапа 8 "LAN Release", который этот чекбокс
уже частично реализует раньше срока, см. пометку в дорожной карте).

Async-вариант `zeroconf` (`zeroconf.asyncio.AsyncZeroconf`), а не
синхронный `zeroconf.Zeroconf` -- Remote целиком asyncio/FastAPI (см.
app.py, lifespan), синхронный `Zeroconf()` завёл бы свой отдельный
поток исподтишка и был бы чужеродным вкраплением; async-вариант
регистрируется/снимается прямо внутри уже существующего
async-`lifespan`, тем же паттерном, что и запуск/остановка
GenerationWatcher рядом.
"""

from __future__ import annotations

import logging
import socket
from typing import Optional

from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from ..launcher.core.remote_process import get_lan_ip

log = logging.getLogger("comfyui_studio.remote")

# Тип сервиса DNS-SD -- см. https://www.rfc-editor.org/rfc/rfc6763 про
# формат "_service._proto.local.". Собственный тип (не общий "_http"),
# чтобы телефон, сканирующий сеть, отличал именно ComfyUI Studio от
# случайных других HTTP-серверов в той же Wi-Fi -- то же имя ожидает
# использовать будущий NsdManager на Android (см. докстринг модуля,
# "без изменений от версии 1" -- при реализации Android-стороны нужно
# использовать РОВНО эту же строку).
SERVICE_TYPE = "_comfyuistudio._tcp.local."


class MdnsAdvertiser:
    """Один экземпляр на процесс Remote -- владеет и `AsyncZeroconf`, и
    зарегистрированным `ServiceInfo`, отвечает за то, чтобы снятие
    регистрации (`stop()`) было безопасно вызывать даже если `start()`
    ничего не зарегистрировал (host был `127.0.0.1`) или упал с ошибкой
    (см. try/except в start())."""

    def __init__(self) -> None:
        self._zeroconf: Optional[AsyncZeroconf] = None
        self._service_info: Optional[ServiceInfo] = None

    async def start(self, host: str, port: int, studio_version: str) -> None:
        if host != "0.0.0.0":
            # См. докстринг модуля -- нет смысла объявлять сервис,
            # слушающий только localhost.
            return

        advertise_ip = get_lan_ip()
        if advertise_ip is None:
            log.warning(
                "mDNS: не удалось определить LAN-адрес этого ПК, "
                "объявление Remote в сети пропущено (сам Remote при "
                "этом продолжает слушать 0.0.0.0 как обычно)."
            )
            return

        hostname = socket.gethostname()
        # Имя сервиса должно быть уникальным в сети -- имя компьютера
        # (обычно и так уникально в домашней/офисной LAN) в скобках,
        # чтобы человек с несколькими ПК Studio сразу отличил нужный.
        service_name = f"ComfyUI Studio ({hostname}).{SERVICE_TYPE}"

        try:
            info = ServiceInfo(
                SERVICE_TYPE,
                service_name,
                addresses=[socket.inet_aton(advertise_ip)],
                port=port,
                properties={"version": studio_version},
                server=f"{hostname}.local.",
            )
            zeroconf = AsyncZeroconf()
            await zeroconf.async_register_service(info)
        except Exception:
            # mDNS -- удобство обнаружения, не критичная функция (см.
            # докстринг модуля) -- сбой здесь не должен мешать Remote
            # продолжать работать по прямому IP, как и раньше.
            log.exception("mDNS: не удалось объявить Remote в сети")
            return

        self._zeroconf = zeroconf
        self._service_info = info
        log.info("mDNS: Remote объявлен в сети как %r (%s:%s)", service_name, advertise_ip, port)

    async def stop(self) -> None:
        if self._zeroconf is None:
            return
        try:
            if self._service_info is not None:
                await self._zeroconf.async_unregister_service(self._service_info)
            await self._zeroconf.async_close()
        except Exception:
            log.exception("mDNS: ошибка при снятии объявления Remote")
        finally:
            self._zeroconf = None
            self._service_info = None
