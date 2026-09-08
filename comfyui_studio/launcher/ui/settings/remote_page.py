"""Раздел "Удалённый доступ" единого дерева настроек: включение
Remote (comfyui_studio/remote/, см. ComfyUIStudio_Remote_Roadmap.md,
этап 1 — "Remote API Core"), pairing телефона и список сопряжённых
устройств.

Независимый переключатель от "Интерфейс" (ComfyUI/Imagine) в
comfyui_page.py — см. §0.1 дорожной карты: Remote можно включить даже
тогда, когда ComfyUI ещё не запускался вовсе.

Эта страница НЕ управляет процессом Remote и не делает HTTP-вызовов
сама — она только собирает ввод пользователя (enable/port) и эмитит
сигналы (enable_toggled/pairing_requested/...), а получает результат
обратно через публичные методы (set_running_state/show_pairing_code/...),
вызываемые из launcher_window.py, которому и принадлежит сам
RemoteProcess (тот же принцип разделения, что и у ComfyUISettingsPage
не запускающей ComfyProcess самостоятельно).

Все строки на этой странице -- исходные на русском (см. пояснение в
general_page.py про TRANSLATIONS/loc.tr()).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class RemoteSettingsPage(QWidget):
    # см. докстринг класса — эта страница не действует сама, только
    # просит launcher_window.py действовать и ждёт обратного вызова.
    changed = Signal()  # enabled/port изменились -- автосохранение cfg
    enable_toggled = Signal(bool)
    pairing_requested = Signal()
    refresh_devices_requested = Signal()
    revoke_requested = Signal(list)  # list[str] -- device_id выбранных строк

    def __init__(self, cfg: dict, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc
        remote_cfg = cfg.get("remote", {})
        self._loading_fields = False
        # device_id каждой строки таблицы устройств -- параллельный
        # список вместо хранения в самом QTableWidgetItem (проще, чем
        # городить кастомный item с data(Qt.UserRole) ради одного поля).
        self._device_ids: list[str] = []

        root = QVBoxLayout(self)

        # -- Включение ------------------------------------------------
        remote_box = QGroupBox(self._tr("Удалённый доступ"))
        self.remote_box = remote_box
        remote_form = QFormLayout(remote_box)

        self.hint_label = QLabel(
            self._tr(
                "Доступ к ComfyUI Studio с телефона в той же локальной "
                "сети (Remote API, этапы 1–4 дорожной карты) — pairing, "
                "realtime-статус и «терминал» с Imagine внутри уже "
                "работают, Android-приложения пока нет (см. README)."
            )
        )
        self.hint_label.setWordWrap(True)
        self.hint_label.setObjectName("mutedLabel")
        remote_form.addRow(self.hint_label)

        self.enable_check = QCheckBox(self._tr("Включить удалённый доступ"))
        self.enable_check.setChecked(bool(remote_cfg.get("enabled", False)))
        self.enable_check.stateChanged.connect(self._on_enable_changed)
        remote_form.addRow(self.enable_check)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(remote_cfg.get("port", 7861)))
        self.port_spin.valueChanged.connect(self._on_field_changed)
        self.port_row_label = QLabel(self._tr("Порт Remote:"))
        remote_form.addRow(self.port_row_label, self.port_spin)

        # НОВОЕ: по умолчанию Remote слушает только 127.0.0.1 (см. §1.5/
        # §8 дорожной карты -- LAN-доступ "из коробки" сознательно не
        # включён, чтобы Remote не оказался доступен всей сети без явного
        # согласия). Этот чекбокс -- заранее реализованный кусочек
        # этапа 8 ("LAN Release"), понадобившийся раньше срока: без него
        # телефон в принципе не может достучаться до Remote, даже если
        # всё остальное (pairing, прокси к Imagine) уже работает.
        self.lan_access_check = QCheckBox(
            self._tr("Разрешить доступ по локальной сети (не только с этого ПК)")
        )
        self.lan_access_check.setChecked(remote_cfg.get("host", "127.0.0.1") != "127.0.0.1")
        self.lan_access_check.stateChanged.connect(self._on_field_changed)
        remote_form.addRow(self.lan_access_check)

        self.lan_warning_label = QLabel(
            self._tr(
                "Remote начнёт слушать все сетевые интерфейсы этого ПК, "
                "а не только localhost -- любое устройство в той же "
                "локальной сети сможет обращаться к нему (без токена "
                "устройства доступа к данным всё равно не получит, см. "
                "pairing выше). Также может понадобиться разрешить порт "
                "в брандмауэре Windows."
            )
        )
        self.lan_warning_label.setWordWrap(True)
        self.lan_warning_label.setObjectName("mutedLabel")
        remote_form.addRow(self.lan_warning_label)

        self.status_label = QLabel(self._tr("Остановлен."))
        self.status_label.setObjectName("mutedLabel")
        remote_form.addRow(self.status_label)

        self.lan_url_label = QLabel("")
        self.lan_url_label.setWordWrap(True)
        self.lan_url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        remote_form.addRow(self.lan_url_label)

        root.addWidget(remote_box)

        # -- Pairing ----------------------------------------------------
        pairing_box = QGroupBox(self._tr("Подключить телефон"))
        self.pairing_box = pairing_box
        pairing_layout = QVBoxLayout(pairing_box)

        self.pair_btn = QPushButton(self._tr("Получить код для телефона"))
        self.pair_btn.clicked.connect(self.pairing_requested.emit)
        pairing_layout.addWidget(self.pair_btn)

        self.pairing_result_label = QLabel("")
        self.pairing_result_label.setWordWrap(True)
        pairing_layout.addWidget(self.pairing_result_label)

        root.addWidget(pairing_box)

        # -- Push-уведомления (§Этап 6.5 дорожной карты) -----------------
        # Заводить/настраивать сам Firebase-проект -- вручную в консоли
        # Google (это единственное, что эта страница в принципе не может
        # сделать за пользователя, см. ComfyUIStudio_Remote_Roadmap.md,
        # этап 6.5, п.1 "Реализация") -- здесь только путь к уже
        # скачанному JSON-файлу сервис-аккаунта.
        push_box = QGroupBox(self._tr("Push-уведомления"))
        self.push_box = push_box
        push_form = QFormLayout(push_box)

        self.push_hint_label = QLabel(
            self._tr(
                "Уведомление о готовой генерации, даже если приложение на "
                "телефоне свёрнуто (Firebase Cloud Messaging, бесплатно). "
                "Заведите проект на console.firebase.google.com, скачайте "
                "JSON-файл сервисного аккаунта и укажите его здесь; поле "
                "пустое -- push просто не отправляются, остальной "
                "удалённый доступ на это никак не влияет."
            )
        )
        self.push_hint_label.setWordWrap(True)
        self.push_hint_label.setObjectName("mutedLabel")
        push_form.addRow(self.push_hint_label)

        push_path_row = QHBoxLayout()
        self.fcm_path_edit = QLineEdit(remote_cfg.get("fcm_service_account_path") or "")
        self.fcm_path_edit.setReadOnly(True)
        self.fcm_path_edit.setPlaceholderText(self._tr("Файл не выбран"))
        self.fcm_browse_btn = QPushButton(self._tr("Обзор…"))
        self.fcm_browse_btn.clicked.connect(self._on_browse_fcm_path)
        self.fcm_clear_btn = QPushButton(self._tr("Очистить"))
        self.fcm_clear_btn.clicked.connect(self._on_clear_fcm_path)
        push_path_row.addWidget(self.fcm_path_edit, 1)
        push_path_row.addWidget(self.fcm_browse_btn)
        push_path_row.addWidget(self.fcm_clear_btn)
        self.fcm_path_row_label = QLabel(self._tr("Файл сервис-аккаунта:"))
        push_form.addRow(self.fcm_path_row_label, push_path_row)

        root.addWidget(push_box)

        # -- Устройства ---------------------------------------------------
        devices_box = QGroupBox(self._tr("Сопряжённые устройства"))
        self.devices_box = devices_box
        devices_layout = QVBoxLayout(devices_box)

        self.devices_table = QTableWidget(0, 4)
        self.devices_table.setHorizontalHeaderLabels([
            self._tr("Имя"),
            self._tr("Создано"),
            self._tr("Последний раз в сети"),
            self._tr("Статус"),
        ])
        self.devices_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self.devices_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.devices_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        devices_layout.addWidget(self.devices_table)

        devices_btn_row = QHBoxLayout()
        self.refresh_devices_btn = QPushButton(self._tr("Обновить"))
        self.refresh_devices_btn.clicked.connect(self.refresh_devices_requested.emit)
        self.revoke_devices_btn = QPushButton(self._tr("Отозвать выбранные"))
        self.revoke_devices_btn.clicked.connect(self._on_revoke_clicked)
        devices_btn_row.addWidget(self.refresh_devices_btn)
        devices_btn_row.addWidget(self.revoke_devices_btn)
        devices_btn_row.addStretch(1)
        devices_layout.addLayout(devices_btn_row)

        root.addWidget(devices_box)
        root.addStretch(1)

        self._on_enable_changed()

    # -- ввод пользователя -> сигналы наружу -------------------------------

    def _on_field_changed(self, *_args):
        self.changed.emit()

    def _on_enable_changed(self, *_args):
        enabled = self.enable_check.isChecked()
        self.pairing_box.setEnabled(enabled)
        self.devices_box.setEnabled(enabled)
        self.changed.emit()
        self.enable_toggled.emit(enabled)

    def _on_revoke_clicked(self):
        rows = sorted({idx.row() for idx in self.devices_table.selectedIndexes()})
        device_ids = [
            self._device_ids[row] for row in rows if row < len(self._device_ids)
        ]
        if device_ids:
            self.revoke_requested.emit(device_ids)

    def _on_browse_fcm_path(self):
        path, _filter = QFileDialog.getOpenFileName(
            self,
            self._tr("Выберите JSON-файл сервис-аккаунта Firebase"),
            "",
            "JSON (*.json)",
        )
        if path:
            self.fcm_path_edit.setText(path)
            self._on_field_changed()

    def _on_clear_fcm_path(self):
        if self.fcm_path_edit.text():
            self.fcm_path_edit.setText("")
            self._on_field_changed()

    # -- обратные вызовы из launcher_window.py -----------------------------

    def set_running_state(self, running: bool, error: str | None = None) -> None:
        """Вызывается launcher_window.py после попытки старта/остановки
        RemoteProcess -- error задаётся, только если попытка запуска
        провалилась (см. RuntimeError в RemoteProcess.start()).

        ВАЖНО: port_spin/lan_access_check НЕ блокируются на время работы
        Remote (было так до 2026-09-06 -- см. историю этого файла) --
        из-за автозапуска Remote при старте Studio (см. launcher_window.
        __init__) поле почти всегда оказывалось заблокировано уже к
        моменту, когда пользователь открывал настройки, и клик по
        чекбоксу просто ничего не делал -- пользователь менял видимое
        состояние, но событие stateChanged не срабатывало, ничего не
        сохранялось. Теперь поля всегда редактируемые, а о том, что для
        применения нужен перезапуск Remote, говорит status_label ниже."""
        if error:
            self.status_label.setText(self._tr("Ошибка запуска: {}").format(error))
        elif running:
            self.status_label.setText(
                self._tr(
                    "Запущен, порт {}. Изменения порта/сети применятся "
                    "после перезапуска -- выключите и включите переключатель выше."
                ).format(self.port_spin.value())
            )
        else:
            self.status_label.setText(self._tr("Остановлен."))
        if not running:
            self.lan_url_label.setText("")

    def show_lan_url(self, url: str | None) -> None:
        """Вызывается launcher_window.py после успешного старта, если
        включён доступ по локальной сети (см. lan_access_check) и
        удалось определить LAN-адрес этого ПК (см.
        remote_process.get_lan_ip) -- готовая ссылка для открытия с
        телефона, чтобы не заставлять человека самому искать IP этого
        ПК в настройках Windows."""
        if url:
            self.lan_url_label.setText(
                self._tr("Адрес для телефона (в этой же сети): {}").format(url)
            )
        else:
            self.lan_url_label.setText("")

    def show_pairing_code(self, code: str, expires_at_text: str, attempts_left: int) -> None:
        self.pairing_result_label.setText(
            self._tr(
                "Код: {} (действителен до {}, осталось попыток: {}). "
                "Введите его в приложении на телефоне."
            ).format(code, expires_at_text, attempts_left)
        )

    def show_pairing_error(self, message: str) -> None:
        self.pairing_result_label.setText(
            self._tr("Не удалось получить код: {}").format(message)
        )

    def set_devices(self, devices: list[dict]) -> None:
        """devices -- список словарей с ключами device_id/name/
        created_at/last_seen/revoked (см. DeviceInfo из remote/models.py,
        уже сериализованный в JSON и разобранный обратно в launcher_window.py)."""
        self._device_ids = [d.get("device_id", "") for d in devices]
        self.devices_table.setRowCount(len(devices))
        for row, d in enumerate(devices):
            self.devices_table.setItem(row, 0, QTableWidgetItem(d.get("name", "")))
            self.devices_table.setItem(row, 1, QTableWidgetItem(d.get("created_at") or ""))
            self.devices_table.setItem(row, 2, QTableWidgetItem(d.get("last_seen") or "—"))
            status = self._tr("Отозвано") if d.get("revoked") else self._tr("Активно")
            self.devices_table.setItem(row, 3, QTableWidgetItem(status))

    def show_devices_error(self, message: str) -> None:
        self.devices_table.setRowCount(0)
        self._device_ids = []
        self.pairing_result_label.setText(
            self._tr("Не удалось получить список устройств: {}").format(message)
        )

    # -- сбор состояния для автосохранения ---------------------------------

    def collect(self) -> dict:
        return {
            "remote": {
                "enabled": self.enable_check.isChecked(),
                "port": self.port_spin.value(),
                "host": "0.0.0.0" if self.lan_access_check.isChecked() else "127.0.0.1",
                # НОВОЕ (§Этап 6.5) -- пусто (не None), если поле не
                # заполнено; comfyui_studio/remote/fcm.py трактует и то,
                # и другое одинаково ("push не настроен").
                "fcm_service_account_path": self.fcm_path_edit.text().strip() or None,
            }
        }

    # -- прочее -----------------------------------------------------

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.remote_box.setTitle(self._tr("Удалённый доступ"))
        self.hint_label.setText(
            self._tr(
                "Доступ к ComfyUI Studio с телефона в той же локальной "
                "сети (Remote API, этапы 1–4 дорожной карты) — pairing, "
                "realtime-статус и «терминал» с Imagine внутри уже "
                "работают, Android-приложения пока нет (см. README)."
            )
        )
        self.enable_check.setText(self._tr("Включить удалённый доступ"))
        self.port_row_label.setText(self._tr("Порт Remote:"))
        self.lan_access_check.setText(
            self._tr("Разрешить доступ по локальной сети (не только с этого ПК)")
        )
        self.lan_warning_label.setText(
            self._tr(
                "Remote начнёт слушать все сетевые интерфейсы этого ПК, "
                "а не только localhost -- любое устройство в той же "
                "локальной сети сможет обращаться к нему (без токена "
                "устройства доступа к данным всё равно не получит, см. "
                "pairing выше). Также может понадобиться разрешить порт "
                "в брандмауэре Windows."
            )
        )
        self.pairing_box.setTitle(self._tr("Подключить телефон"))
        self.pair_btn.setText(self._tr("Получить код для телефона"))
        self.push_box.setTitle(self._tr("Push-уведомления"))
        self.push_hint_label.setText(
            self._tr(
                "Уведомление о готовой генерации, даже если приложение на "
                "телефоне свёрнуто (Firebase Cloud Messaging, бесплатно). "
                "Заведите проект на console.firebase.google.com, скачайте "
                "JSON-файл сервисного аккаунта и укажите его здесь; поле "
                "пустое -- push просто не отправляются, остальной "
                "удалённый доступ на это никак не влияет."
            )
        )
        self.fcm_path_row_label.setText(self._tr("Файл сервис-аккаунта:"))
        self.fcm_path_edit.setPlaceholderText(self._tr("Файл не выбран"))
        self.fcm_browse_btn.setText(self._tr("Обзор…"))
        self.fcm_clear_btn.setText(self._tr("Очистить"))
        self.devices_box.setTitle(self._tr("Сопряжённые устройства"))
        self.devices_table.setHorizontalHeaderLabels([
            self._tr("Имя"),
            self._tr("Создано"),
            self._tr("Последний раз в сети"),
            self._tr("Статус"),
        ])
        self.refresh_devices_btn.setText(self._tr("Обновить"))
        self.revoke_devices_btn.setText(self._tr("Отозвать выбранные"))
