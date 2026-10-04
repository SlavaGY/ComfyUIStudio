"""
Общее хранилище конфигурации лаунчера (этап R7).

До R7 у конфигурации было несколько «владельцев»: MainWindow.cfg,
SettingsPage.cfg, AppSettingsDialog.cfg (и копии на страницах). Диалог
автосохранял правки в СВОЙ снимок, а остальные держали устаревшие. Отсюда
обходные пути: SettingsPage._on_launch брал cfg «из диалога, а не из
себя», RemoteController.start() перечитывал config.json с диска, а само
чтение с диска могло опередить автосохранение (дебаунс 400 мс), и Remote
стартовал по старому порту.

ConfigStore — один источник правды:

  * `cfg` — текущий снимок (dict). Каждое изменение создаёт НОВЫЙ dict
    (copy-on-write), поэтому тот, кто взял снимок (например, запуск
    ComfyUI запоминает cfg на момент нажатия «Запустить»), видит его
    неизменным; кто хочет актуальное — читает store.cfg заново.
    Снимок нельзя менять на месте: только через update()/replace.
  * update(changes) — поверхностное слияние (как раньше
    `cfg.update(page.collect())`), запись на диск и сигнал changed, но
    только если что-то реально изменилось (force_save=True пишет на диск
    и без изменений — так автосохранение диалога ведёт себя, как раньше).
  * save() — безусловная запись текущего снимка (запуск ComfyUI).
  * reload() — перечитать с диска (после сброса настроек).
  * reset() — значения по умолчанию, запись, сигнал.

Только GUI-поток. Процессы Remote/Imagine по-прежнему читают
config.json с диска при своём старте — это отдельные процессы.
"""

from __future__ import annotations

import copy

from PySide6.QtCore import QObject, Signal

from .config import load_config, save_config
from .constants import DEFAULT_CONFIG


class ConfigStore(QObject):
    changed = Signal(dict)

    def __init__(self, initial=None, load=None, save=None, parent=None):
        """initial — стартовый снимок (иначе читается с диска).
        load/save — подмена чтения/записи (тесты, а диалог настроек
        передаёт save, ссылающийся на свой модульный save_config)."""
        super().__init__(parent)
        self._load = load
        self._save = save
        self._cfg = dict(initial) if initial is not None else self._read()

    def _read(self):
        return (self._load or load_config)()

    def _persist(self):
        (self._save or save_config)(self._cfg)

    @property
    def cfg(self):
        return self._cfg

    def get(self, key, default=None):
        return self._cfg.get(key, default)

    def update(self, changes, force_save=False):
        """Сливает changes в конфиг. True, если что-то изменилось."""
        new = dict(self._cfg)
        new.update(changes)
        if new == self._cfg:
            if force_save:
                self._persist()
            return False
        self._cfg = new
        self._persist()
        self.changed.emit(new)
        return True

    def save(self):
        self._persist()

    def reload(self):
        self._cfg = self._read()
        self.changed.emit(self._cfg)

    def reset(self):
        self._cfg = copy.deepcopy(DEFAULT_CONFIG)
        self._persist()
        self.changed.emit(self._cfg)
