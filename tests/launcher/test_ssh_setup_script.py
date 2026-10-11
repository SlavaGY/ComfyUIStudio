"""Поиск Setup-SshAccess.ps1 для кнопки «Настроить SSH-доступ» (этап 3
аудита): из исходников и из собранного exe (sys._MEIPASS)."""

import os

import pytest

pytest.importorskip("PySide6.QtCore")

from comfyui_studio.launcher.core import ssh_setup  # noqa: E402


def test_script_found_when_running_from_sources():
    assert ssh_setup.find_script() == ssh_setup.SCRIPT_PATH
    assert os.path.isfile(ssh_setup.SCRIPT_PATH)


def test_script_falls_back_to_bundle_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ssh_setup, "SCRIPT_PATH", str(tmp_path / "missing" / "x.ps1"))
    bundled = tmp_path / "tools" / "ssh_setup"
    bundled.mkdir(parents=True)
    (bundled / ssh_setup.SCRIPT_NAME).write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(ssh_setup.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert ssh_setup.find_script() == str(bundled / ssh_setup.SCRIPT_NAME)


def test_script_none_when_nowhere(tmp_path, monkeypatch):
    monkeypatch.setattr(ssh_setup, "SCRIPT_PATH", str(tmp_path / "x.ps1"))
    monkeypatch.delattr(ssh_setup.sys, "_MEIPASS", raising=False)

    assert ssh_setup.find_script() is None
