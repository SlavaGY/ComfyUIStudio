"""
Общие fixtures для tests/remote.

Зачем нужна автоматическая изоляция push (autouse).
GenerationWatcher на каждое generation.completed / generation.error
вызывает fcm.send_generation_push -- а тот читает токены устройств из
НАСТОЯЩЕГО %APPDATA%\\ComfyUIStudio\\remote_devices.json и сервис-аккаунт
из НАСТОЯЩЕГО config.json и шлёт реальное уведомление в Firebase. Без
подмены любой прогон test_generation_watcher.py на машине, где Remote уже
настроен и телефон спарен, присылал бы разработчику настоящие push
(\"Готово\" / \"Ошибка генерации\"). Поэтому для ВСЕХ тестов этого каталога:

  * fcm.send_generation_push заменён записью события в список
    `sent_pushes` -- в нём можно проверить, ЧТО ушло бы в push;
  * DEVICE_STORE_PATH указывает на временный файл, чтобы реальные токены
    устройств не читались даже при обходных путях.

Тесты, которым нужен другой путь к хранилищу, по-прежнему могут сами
подменять DEVICE_STORE_PATH (их monkeypatch применяется позже этого).
"""

import pytest


@pytest.fixture(autouse=True)
def sent_pushes(monkeypatch, tmp_path):
    from comfyui_studio.remote import device_store, fcm

    sent = []
    monkeypatch.setattr(fcm, "send_generation_push", lambda event: sent.append(event))
    monkeypatch.setattr(
        device_store, "DEVICE_STORE_PATH", str(tmp_path / "remote_devices.json")
    )
    return sent
