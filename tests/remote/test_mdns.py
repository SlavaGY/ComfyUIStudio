"""
Тесты comfyui_studio/remote/mdns.py -- логика "объявлять/не объявлять"
(host != "0.0.0.0", LAN IP не определился) и корректная передача
параметров в ServiceInfo/AsyncZeroconf. Реальная сетевая рассылка mDNS
не тестируется здесь (как и реальный WS/HTTP в предыдущих этапах) --
`AsyncZeroconf` подменяется фейком, ничего в сеть не уходит.

Тестовые функции синхронные, оборачивают `await` через `asyncio.run()`
-- в проекте нет pytest-asyncio/anyio (см. ту же оговорку в
tests/imagine/test_progress_forwarder.py и tests/remote/
test_generation_watcher.py).
"""

import asyncio

import pytest

from comfyui_studio.remote import mdns


class FakeAsyncZeroconf:
    """Записывает вызовы вместо реальной сетевой активности."""

    instances = []

    def __init__(self):
        self.registered = []
        self.unregistered = []
        self.closed = False
        FakeAsyncZeroconf.instances.append(self)

    async def async_register_service(self, info):
        self.registered.append(info)

    async def async_unregister_service(self, info):
        self.unregistered.append(info)

    async def async_close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def reset_fakes(monkeypatch):
    FakeAsyncZeroconf.instances = []
    monkeypatch.setattr(mdns, "AsyncZeroconf", FakeAsyncZeroconf)
    yield


def test_start_skips_when_host_is_localhost_only(monkeypatch):
    monkeypatch.setattr(mdns, "get_lan_ip", lambda: "192.168.1.50")
    advertiser = mdns.MdnsAdvertiser()

    asyncio.run(advertiser.start("127.0.0.1", 7861, "0.1.0"))

    assert FakeAsyncZeroconf.instances == []
    assert advertiser._zeroconf is None


def test_start_skips_when_lan_ip_unknown(monkeypatch):
    monkeypatch.setattr(mdns, "get_lan_ip", lambda: None)
    advertiser = mdns.MdnsAdvertiser()

    asyncio.run(advertiser.start("0.0.0.0", 7861, "0.1.0"))

    assert FakeAsyncZeroconf.instances == []


def test_start_registers_service_when_lan_enabled(monkeypatch):
    monkeypatch.setattr(mdns, "get_lan_ip", lambda: "192.168.1.50")
    advertiser = mdns.MdnsAdvertiser()

    asyncio.run(advertiser.start("0.0.0.0", 7861, "0.1.0"))

    assert len(FakeAsyncZeroconf.instances) == 1
    zc = FakeAsyncZeroconf.instances[0]
    assert len(zc.registered) == 1
    info = zc.registered[0]
    assert info.port == 7861
    assert info.type == mdns.SERVICE_TYPE
    assert advertiser._zeroconf is zc
    assert advertiser._service_info is info


def test_stop_without_start_is_noop():
    advertiser = mdns.MdnsAdvertiser()
    asyncio.run(advertiser.stop())  # не должно бросать исключение


def test_stop_unregisters_and_closes(monkeypatch):
    monkeypatch.setattr(mdns, "get_lan_ip", lambda: "192.168.1.50")
    advertiser = mdns.MdnsAdvertiser()
    asyncio.run(advertiser.start("0.0.0.0", 7861, "0.1.0"))
    zc = FakeAsyncZeroconf.instances[0]

    asyncio.run(advertiser.stop())

    assert len(zc.unregistered) == 1
    assert zc.closed is True
    assert advertiser._zeroconf is None
    assert advertiser._service_info is None


def test_start_survives_registration_error(monkeypatch):
    monkeypatch.setattr(mdns, "get_lan_ip", lambda: "192.168.1.50")

    class ExplodingZeroconf(FakeAsyncZeroconf):
        async def async_register_service(self, info):
            raise OSError("сеть недоступна")

    monkeypatch.setattr(mdns, "AsyncZeroconf", ExplodingZeroconf)
    advertiser = mdns.MdnsAdvertiser()

    # Не должно бросать исключение наружу -- см. докстринг модуля про
    # то, что mDNS не критичен для работы самого Remote.
    asyncio.run(advertiser.start("0.0.0.0", 7861, "0.1.0"))

    assert advertiser._zeroconf is None
