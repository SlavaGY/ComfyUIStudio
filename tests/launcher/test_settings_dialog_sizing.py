"""Размер и прокрутка окна настроек (launcher/ui/settings/app_settings_dialog.py):
стартовый размер вписывается в экран, а страницы без собственной прокрутки
получают её, как страница ComfyUI. Максимальный размер не ограничивается --
пользователь волен растянуть окно сам."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QRect, QSize  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from comfyui_studio import shared_promptgen as sp  # noqa: E402
from comfyui_studio.launcher.ui.settings import app_settings_dialog as mod  # noqa: E402

PREFERRED = QSize(900, 640)


def inside(inner: QRect, outer: QRect) -> bool:
    return (
        inner.left() >= outer.left() and inner.top() >= outer.top()
        and inner.right() <= outer.right() and inner.bottom() <= outer.bottom()
    )


# -- чистая функция: любые экраны ---------------------------------------------

def test_big_screen_keeps_preferred_size_and_centers():
    avail = QRect(0, 0, 1920, 1040)
    geo = mod.initial_geometry(avail, PREFERRED)
    assert geo.size() == PREFERRED
    assert geo.center() in (avail.center(), avail.center() + QPoint(-1, -1), avail.center() + QPoint(0, -1), avail.center() + QPoint(-1, 0))


def test_small_screen_limits_size_to_fraction():
    avail = QRect(0, 0, 1366, 728)  # ноутбук минус панель задач
    geo = mod.initial_geometry(avail, PREFERRED)
    assert geo.width() == 900                      # влезает целиком
    assert geo.height() == int(728 * mod.MAX_SCREEN_FRACTION) == 618
    assert inside(geo, avail)


def test_tiny_screen_limits_both_sides():
    avail = QRect(0, 0, 800, 600)
    geo = mod.initial_geometry(avail, PREFERRED)
    assert geo.size() == QSize(int(800 * 0.85), int(600 * 0.85))
    assert inside(geo, avail)


def test_available_area_with_offset_is_respected():
    # вторая слева колонка / панель задач сверху: availableGeometry не с нуля
    avail = QRect(-1920, 40, 1920, 1000)
    geo = mod.initial_geometry(avail, PREFERRED)
    assert inside(geo, avail)


def test_anchor_near_edge_is_pulled_back_inside_screen():
    avail = QRect(0, 0, 1920, 1040)
    geo = mod.initial_geometry(avail, PREFERRED, anchor_center=QPoint(1900, 1030))
    assert inside(geo, avail) and geo.size() == PREFERRED
    geo = mod.initial_geometry(avail, PREFERRED, anchor_center=QPoint(-500, -500))
    assert inside(geo, avail)


def test_anchor_inside_screen_is_used():
    avail = QRect(0, 0, 3000, 2000)
    geo = mod.initial_geometry(avail, PREFERRED, anchor_center=QPoint(1000, 800))
    assert geo.center().x() in range(999, 1002) and geo.center().y() in range(799, 802)


# -- настоящий диалог ------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(qapp, tmp_path, monkeypatch):
    from comfyui_studio.launcher.core.constants import DEFAULT_CONFIG
    from comfyui_studio.themes.theme_manager import ThemeManager

    monkeypatch.setattr(sp, "SHARED_DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(sp, "SHARED_PROMPTGEN_PATH", str(tmp_path / "shared" / "prompt_generator.json"))
    monkeypatch.setattr(mod, "save_config", lambda cfg: None)
    dlg = mod.AppSettingsDialog(dict(DEFAULT_CONFIG), ThemeManager(), loc=None)
    yield dlg
    dlg.close()


def test_first_show_fits_the_screen(dialog, qapp):
    dialog.show()
    qapp.processEvents()
    avail = dialog.screen().availableGeometry()
    assert dialog.width() <= avail.width() * mod.MAX_SCREEN_FRACTION + 1
    assert dialog.height() <= avail.height() * mod.MAX_SCREEN_FRACTION + 1
    assert inside(dialog.geometry(), avail)


def test_no_maximum_size_and_user_can_resize_beyond_screen(dialog, qapp):
    dialog.show()
    qapp.processEvents()
    assert dialog.maximumSize() == QSize(16777215, 16777215)
    avail = dialog.screen().availableGeometry()
    dialog.resize(avail.width() + 400, avail.height() + 300)
    qapp.processEvents()
    assert dialog.width() == avail.width() + 400


def test_reopening_does_not_reset_user_geometry(dialog, qapp):
    dialog.show()
    qapp.processEvents()
    dialog.resize(520, 400)
    dialog.move(30, 40)
    dialog.hide()
    dialog.show()
    qapp.processEvents()
    assert dialog.size() == QSize(520, 400)


def test_every_page_scrolls_when_window_is_too_small(dialog, qapp):
    """Страница без собственной прокрутки лежит внутри QScrollArea, а у
    страниц ComfyUI/Генератора она своя -- ни одна не «обрезается»."""
    assert dialog.stack.count() == len(dialog._sections)
    for index, (_title, page) in enumerate(dialog._sections):
        holder = dialog.stack.widget(index)
        if holder is page:
            assert page.findChild(QScrollArea) is not None, "страница без прокрутки"
        else:
            assert isinstance(holder, QScrollArea) and holder.widget() is page
            assert holder.widgetResizable()


def test_dialog_can_be_made_small_without_pages_forcing_it_big(dialog, qapp):
    dialog.show()
    dialog.resize(mod.MIN_DIALOG_SIZE)
    qapp.processEvents()
    assert dialog.width() <= mod.MIN_DIALOG_SIZE.width() + 2
    assert dialog.height() <= mod.MIN_DIALOG_SIZE.height() + 2
