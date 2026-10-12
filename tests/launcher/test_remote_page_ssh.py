"""Блок «Доступ вне дома (SSH)» на странице настроек Remote (этап 3
аудита): страница только собирает ввод и показывает результат, запуск
скрипта и отправку ключа делает RemoteController (см. его тесты)."""

import pytest

from comfyui_studio.launcher.ui.settings import remote_page as rp
from comfyui_studio.launcher.ui.settings.remote_page import RemoteSettingsPage


@pytest.fixture
def make(qtbot):
    def _make(**remote):
        page = RemoteSettingsPage({"remote": remote})
        qtbot.addWidget(page)
        return page

    return _make


def test_collect_includes_ssh_port_with_default(make):
    assert make().collect()["remote"]["ssh_port"] == 2222


def test_collect_keeps_configured_ssh_port(make):
    page = make(ssh_port=2300)

    assert page.ssh_port_spin.value() == 2300
    assert page.collect()["remote"]["ssh_port"] == 2300


def test_changing_ssh_port_triggers_autosave(make, qtbot):
    page = make()

    with qtbot.waitSignal(page.changed):
        page.ssh_port_spin.setValue(2400)


def test_button_emits_setup_request(make, qtbot):
    page = make(enabled=True)

    with qtbot.waitSignal(page.ssh_setup_requested):
        page.ssh_setup_btn.click()


def test_ssh_box_follows_remote_enabled_switch(make):
    page = make(enabled=False)
    assert not page.ssh_box.isEnabled()

    page.enable_check.setChecked(True)

    assert page.ssh_box.isEnabled()


def test_busy_state_blocks_button_and_port(make):
    page = make(enabled=True)

    page.set_ssh_busy(True)
    assert not page.ssh_setup_btn.isEnabled() and not page.ssh_port_spin.isEnabled()
    assert page.ssh_status_label.text()

    page.set_ssh_busy(False)
    assert page.ssh_setup_btn.isEnabled() and page.ssh_port_spin.isEnabled()


def test_result_shows_host_version_and_warnings_and_unblocks(make):
    page = make(enabled=True)
    page.set_ssh_busy(True)

    page.show_ssh_result("2001:db8::5", 4, ["Match-блок уже есть"])

    text = page.ssh_status_label.text()
    assert "2001:db8::5" in text and "4" in text and "Match-блок уже есть" in text
    assert page.ssh_setup_btn.isEnabled()


def test_result_without_host_explains_phone_will_not_get_profile(make):
    page = make(enabled=True)

    page.show_ssh_result("", 4, [])

    assert "не получит профиль" in page.ssh_status_label.text()


def test_error_is_shown_and_unblocks(make):
    page = make(enabled=True)
    page.set_ssh_busy(True)

    page.show_ssh_error("Отменено")

    assert "Отменено" in page.ssh_status_label.text()
    assert page.ssh_setup_btn.isEnabled()


def test_choose_host_returns_stripped_text(make, monkeypatch):
    seen = {}

    def get_item(parent, title, label, items, current, editable):
        seen["items"], seen["editable"] = items, editable
        return " 2001:db8::5 ", True

    monkeypatch.setattr(rp.QInputDialog, "getItem", staticmethod(get_item))

    assert make().choose_ssh_host(["2001:db8::5", "fd00::1"]) == "2001:db8::5"
    assert seen == {"items": ["2001:db8::5", "fd00::1"], "editable": True}


def test_choose_host_cancel_returns_none(make, monkeypatch):
    monkeypatch.setattr(
        rp.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False))
    )

    assert make().choose_ssh_host([]) is None
