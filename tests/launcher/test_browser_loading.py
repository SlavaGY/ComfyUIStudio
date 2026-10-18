"""Навигация ждёт готовности профиля, а отменённая загрузка не оживает."""

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from comfyui_studio.launcher.ui import browser_page as browser
from comfyui_studio.launcher.ui.rvc_browser_page import RvcBrowserPage


class Profile(QObject):
    clearHttpCacheCompleted = Signal()
    downloadRequested = Signal(object)

    HttpCacheType = browser.QWebEngineProfile.HttpCacheType

    def __init__(self, name, parent):
        super().__init__(parent)
        self.clearing = False
        self.clear_count = 0

    def setPersistentStoragePath(self, path):
        pass

    def setHttpCacheType(self, kind):
        assert kind == self.HttpCacheType.NoCache

    def clearHttpCache(self):
        self.clearing = True
        self.clear_count += 1

    def finish_clear(self):
        self.clearing = False
        self.clearHttpCacheCompleted.emit()


class Page(QObject):
    loadFinished = Signal(bool)

    def __init__(self, profile, *args):
        assert not profile.clearing, "Создание страницы во время очистки профиля"
        super().__init__()

    def setBackgroundColor(self, color):
        self.background = color


class View(QWidget):
    def __init__(self):
        super().__init__()
        self.requests = []

    def setPage(self, page):
        self.current_page = page

    def load(self, url):
        self.requests.append(url.toString())


@pytest.fixture
def rvc(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "QWebEngineProfile", Profile)
    monkeypatch.setattr(browser, "RestrictedWebPage", Page)
    monkeypatch.setattr(browser, "QWebEnginePage", Page)
    monkeypatch.setattr(browser, "QWebEngineView", View)
    monkeypatch.setattr(RvcBrowserPage, "PROFILE_DIR", str(tmp_path / "profile"))
    page = RvcBrowserPage()
    yield page
    page.unload()
    page.deleteLater()


def test_first_load_waits_for_cache_clear_and_shows_loading(rvc):
    rvc.load(7863)
    assert rvc.view.requests == []
    assert rvc._page is None
    assert rvc._browser_stack.currentWidget() is rvc._loading_label
    rvc._profile.finish_clear()
    assert rvc.view.requests == ["http://127.0.0.1:7863/"]
    assert rvc._profile.clear_count == 1
    rvc._page.loadFinished.emit(True)
    assert rvc._page_ready
    assert rvc._browser_stack.currentWidget() is rvc.view


def test_stop_during_clear_cancels_deferred_navigation(rvc):
    rvc.load(7863)
    rvc.unload()
    rvc._profile.finish_clear()
    assert rvc.view.requests == []
    assert rvc._page is None
    rvc.load(7863)
    assert rvc.view.requests == ["http://127.0.0.1:7863/"]
    assert rvc._profile.clear_count == 1


def test_latest_request_wins_while_profile_is_being_cleared(rvc):
    rvc.load(7863)
    rvc.load(9000)
    rvc._profile.finish_clear()
    assert rvc.view.requests == ["http://127.0.0.1:9000/"]


def test_failure_shows_error_and_next_success_restores_browser(rvc):
    rvc.load(7863)
    rvc._profile.finish_clear()
    rvc._page.loadFinished.emit(False)
    assert not rvc._page_ready
    assert rvc._browser_stack.currentWidget() is rvc._loading_label
    assert "Не удалось" in rvc._loading_label.text()
    old_page = rvc._page
    rvc.load(7863)
    old_page.loadFinished.emit(True)
    assert not rvc._page_ready
    assert rvc._browser_stack.currentWidget() is rvc._loading_label
    rvc._page.loadFinished.emit(True)
    assert rvc._page_ready
    assert rvc._browser_stack.currentWidget() is rvc.view


def test_unloaded_page_cannot_mark_a_future_page_ready(rvc):
    rvc.load(7863)
    rvc._profile.finish_clear()
    old_page = rvc._page
    rvc.unload()
    old_page.loadFinished.emit(True)
    assert not rvc._page_ready
    assert rvc._browser_stack.currentWidget() is rvc._loading_label
