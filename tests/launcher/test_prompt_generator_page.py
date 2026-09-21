"""
Тесты страницы «Генератор промптов» в настройках Studio
(launcher/ui/settings/prompt_generator_page.py) и её подключения к
AppSettingsDialog.

Qt запускается без экрана (QT_QPA_PLATFORM=offscreen), настоящий
%APPDATA% не трогается -- shared_promptgen.SHARED_PROMPTGEN_PATH
перенаправляется во временную папку.
"""

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from comfyui_studio import shared_promptgen as sp  # noqa: E402
from comfyui_studio.i18n import TRANSLATIONS  # noqa: E402
from comfyui_studio.launcher.ui.settings.prompt_generator_page import (  # noqa: E402
    PromptGeneratorSettingsPage,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "SHARED_DIR", str(tmp_path / "shared"))
    path = str(tmp_path / "shared" / "prompt_generator.json")
    monkeypatch.setattr(sp, "SHARED_PROMPTGEN_PATH", path)
    return path


class EnLoc:
    """Минимальный аналог LocalizationManager с английским языком."""

    def tr(self, text):
        return TRANSLATIONS["en"].get(text, text)


@pytest.fixture
def llama_setup(tmp_path):
    llama = tmp_path / "llama"
    llama.mkdir()
    (llama / "llama-server.exe").write_bytes(b"")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"")
    return llama, model


def test_page_opens_with_defaults_and_does_not_write_file(qapp, settings_file):
    page = PromptGeneratorSettingsPage()
    assert page.prefix_edit.toPlainText() == sp.DEFAULT_PREFIX
    assert page.ctx_spin.value() == sp.DEFAULT_CTX_SIZE == 8192
    assert page.free_comfy_combo.currentData() == "auto"
    assert page.image_side_spin.value() == 1024
    assert page.llama_dir_edit.text() == ""
    assert not os.path.exists(settings_file)  # простое открытие страницы ничего не пишет


def test_page_loads_existing_settings(qapp, settings_file):
    sp.write_settings({"llama_dir": "D:\\llama", "model_path": "D:\\m.gguf", "prefix": "P: {input}",
                       "gpu_layers": "all", "free_comfy_mode": "never", "image_max_side": 768})
    page = PromptGeneratorSettingsPage()
    assert page.llama_dir_edit.text() == "D:\\llama"
    assert page.model_edit.text() == "D:\\m.gguf"
    assert page.prefix_edit.toPlainText() == "P: {input}"
    assert page.gpu_layers_edit.text() == "all"
    assert page.free_comfy_combo.currentData() == "never"
    assert page.image_side_spin.value() == 768


def test_editing_emits_changed_and_save_writes_file(qapp, settings_file, llama_setup):
    llama, model = llama_setup
    page = PromptGeneratorSettingsPage()
    emitted = []
    page.changed.connect(lambda: emitted.append(1))

    page.llama_dir_edit.setText(str(llama))
    page.model_edit.setText(str(model))
    page.prefix_edit.setPlainText("Custom prefix {input}")
    page.ctx_spin.setValue(4096)
    page.free_comfy_combo.setCurrentIndex(page.free_comfy_combo.findData("always"))
    page.image_side_spin.setValue(896)
    page.extra_args_edit.setText("--reasoning off")
    assert len(emitted) == 7

    assert page.save() is True
    data = json.load(open(settings_file, encoding="utf-8"))
    assert data["llama_dir"] == str(llama)
    assert data["model_path"] == str(model)
    assert data["prefix"] == "Custom prefix {input}"
    assert data["ctx_size"] == 4096
    assert data["free_comfy_mode"] == "always"
    assert data["image_max_side"] == 896
    assert data["extra_args"] == "--reasoning off"
    assert data["mmproj_path"] == ""

    # тот же файл читает и бэкенд Imagine
    assert sp.read_settings()["prefix"] == "Custom prefix {input}"


def test_reset_prefix_button(qapp, settings_file):
    page = PromptGeneratorSettingsPage()
    page.prefix_edit.setPlainText("something else")
    page.prefix_reset_btn.click()
    assert page.prefix_edit.toPlainText() == sp.DEFAULT_PREFIX


def test_gpu_layers_validator_rejects_garbage(qapp, settings_file):
    page = PromptGeneratorSettingsPage()
    validator = page.gpu_layers_edit.validator()
    from PySide6.QtGui import QValidator
    assert validator.validate("99", 0)[0] == QValidator.Acceptable
    assert validator.validate("all", 0)[0] == QValidator.Acceptable
    assert validator.validate("", 0)[0] == QValidator.Acceptable
    assert validator.validate("abc", 0)[0] == QValidator.Invalid


def test_status_line_reflects_paths(qapp, settings_file, llama_setup, tmp_path):
    llama, model = llama_setup
    page = PromptGeneratorSettingsPage()
    assert "Укажите папку" in page.status_label.text()

    page.llama_dir_edit.setText(str(llama))
    page.model_edit.setText(str(tmp_path / "missing.gguf"))
    assert page.status_label.text().startswith("✖")
    assert "не найден" in page.status_label.text()

    page.model_edit.setText(str(model))
    assert page.status_label.text().startswith("✔")

    page.mmproj_edit.setText(str(tmp_path / "no-mmproj.gguf"))
    assert "mmproj" in page.status_label.text() and page.status_label.text().startswith("✖")


def test_status_line_is_translated(qapp, settings_file, llama_setup, tmp_path):
    llama, model = llama_setup
    page = PromptGeneratorSettingsPage(loc=EnLoc())
    assert "Set the llama.cpp folder" in page.status_label.text()
    page.llama_dir_edit.setText(str(llama))
    page.model_edit.setText(str(tmp_path / "nope.gguf"))
    assert page.status_label.text() == f"✖ Model file not found: {tmp_path / 'nope.gguf'}"
    page.model_edit.setText(str(model))
    assert page.status_label.text().startswith("✔ Everything found")
    assert page.model_box.title() == "Model and llama.cpp"


def test_dll_hint_shows_lm_studio_vendor_folder(qapp, settings_file, tmp_path):
    backends = tmp_path / "backends"
    llama = backends / "llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.41.0"
    vendor = backends / "vendor" / "win-llama-cuda12-vendor-v2"
    llama.mkdir(parents=True)
    vendor.mkdir(parents=True)
    (llama / "llama-server.exe").write_bytes(b"")
    (vendor / "cudart64_12.dll").write_bytes(b"")
    model = tmp_path / "m.gguf"
    model.write_bytes(b"")

    page = PromptGeneratorSettingsPage()
    assert page.dll_hint_label.text() == ""
    page.llama_dir_edit.setText(str(llama))
    page.model_edit.setText(str(model))
    assert str(vendor) in page.dll_hint_label.text()


def test_every_translation_key_used_by_page_exists():
    """Все строки, которые страница пропускает через _tr(), и все шаблоны
    сообщений о проблемах должны быть в словаре EN (иначе английский
    интерфейс молча покажет русский текст)."""
    import ast
    import inspect

    from comfyui_studio.launcher.ui.settings import prompt_generator_page as mod

    en = TRANSLATIONS["en"]
    tree = ast.parse(inspect.getsource(mod))
    used = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_tr"):
            used.update(a.value for a in node.args
                        if isinstance(a, ast.Constant) and isinstance(a.value, str))
    used.update(sp.MESSAGES.values())
    used.add(mod._GGUF_FILTER)
    used.add("Генератор промптов")  # заголовок раздела в дереве настроек
    assert [k for k in sorted(used) if k not in en] == []


# ---------------------------------------------------------------------------
# подключение к AppSettingsDialog
# ---------------------------------------------------------------------------

@pytest.fixture
def dialog(qapp, settings_file, monkeypatch):
    from comfyui_studio.launcher.ui.settings import app_settings_dialog as mod
    from comfyui_studio.themes.theme_manager import ThemeManager

    saved = []
    monkeypatch.setattr(mod, "save_config", lambda cfg: saved.append(cfg))
    from comfyui_studio.launcher.core.constants import DEFAULT_CONFIG

    dlg = mod.AppSettingsDialog(dict(DEFAULT_CONFIG), ThemeManager(), loc=None)
    dlg.saved_configs = saved
    yield dlg
    dlg.close()


def test_dialog_has_prompt_generator_section(dialog):
    titles = [item.text(0) for item in dialog._tree_items]
    assert "Генератор промптов" in titles
    idx = titles.index("Генератор промптов")
    assert dialog.stack.widget(idx) is dialog.prompt_generator_page
    # порядок разделов и страниц совпадает
    assert titles == [t for t, _page in dialog._sections]


def test_dialog_autosave_writes_shared_file(dialog, settings_file):
    dialog.prompt_generator_page.model_edit.setText("D:\\models\\x.gguf")
    assert dialog._save_timer.isActive()
    dialog._save_timer.stop()
    dialog._auto_save()
    assert json.load(open(settings_file, encoding="utf-8"))["model_path"] == "D:\\models\\x.gguf"
    assert dialog.saved_configs  # обычный config.json тоже сохранён, как и раньше


def test_dialog_hide_flushes_pending_save(dialog, settings_file):
    dialog.show()
    dialog.prompt_generator_page.llama_dir_edit.setText("D:\\fast-close")
    assert dialog._save_timer.isActive()
    dialog.hide()  # закрыли раньше, чем сработал debounce
    assert json.load(open(settings_file, encoding="utf-8"))["llama_dir"] == "D:\\fast-close"


def test_dialog_retranslate_covers_new_page(dialog):
    dialog.loc = EnLoc()
    for page in (dialog.prompt_generator_page,):
        page.loc = dialog.loc
    dialog.retranslate_ui()
    titles = [item.text(0) for item in dialog._tree_items]
    assert "Prompt generator" in titles
    assert dialog.prompt_generator_page.model_box.title() == "Model and llama.cpp"


def test_free_comfy_combo_has_three_translated_modes(qapp, settings_file):
    page = PromptGeneratorSettingsPage()
    assert [page.free_comfy_combo.itemData(i) for i in range(3)] == ["auto", "always", "never"]
    assert [page.free_comfy_combo.itemText(i) for i in range(3)] == [
        "Автоматически (если не хватает VRAM)", "Всегда", "Никогда"]
    en = PromptGeneratorSettingsPage(loc=EnLoc())
    assert [en.free_comfy_combo.itemText(i) for i in range(3)] == [
        "Automatically (if VRAM is short)", "Always", "Never"]
    # язык не влияет на сохраняемое значение
    assert en.current_settings()["free_comfy_mode"] == "auto"


def test_legacy_free_comfy_first_true_is_shown_as_always(qapp, settings_file):
    os.makedirs(sp.SHARED_DIR, exist_ok=True)
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump({"free_comfy_first": True}, f)
    assert PromptGeneratorSettingsPage().free_comfy_combo.currentData() == "always"


def test_open_logs_button_creates_folder_and_opens_it(qapp, settings_file, monkeypatch, tmp_path):
    from comfyui_studio.launcher.ui.settings import prompt_generator_page as mod

    monkeypatch.setattr(sp, "LOG_DIR", str(tmp_path / "logs"))
    opened = []
    monkeypatch.setattr(mod.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    page = PromptGeneratorSettingsPage()
    page.open_logs_btn.click()
    assert (tmp_path / "logs").is_dir()
    assert opened == [str(tmp_path / "logs")]
