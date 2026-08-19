"""Домашний экран лаунчера: запуск/остановка ComfyUI, живой лог, запуск
остальных инструментов комплекта, кнопка "Settings..." открывающая
единое дерево настроек.

Вынесено из comfyui_launcher.py (этап 1 дорожной карты). Этап 4
("Единое дерево настроек") убрал отсюда все поля конфигурации (тема,
язык, папка ComfyUI, порт, скрипт, аргументы командной строки) — они
переехали в отдельный AppSettingsDialog (см. ui/settings/
app_settings_dialog.py), эта страница больше не смешивает "домашний
экран запуска" и "настройки", как раньше. Сам класс LaunchArgsDialog
удалён отсюда целиком — его содержимое стало разделом "Arguments"
ComfyUISettingsPage (ui/settings/comfyui_page.py).
"""

import gc

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from comfyui_studio.themes.theme_manager import ThemeManager

from ..core.comfy_process import (
    EXTERNAL_APPS,
    ExternalApp,
    launch_external_app,
    resolve_external_launch,
)
from ..core.config import save_config, validate_portable_root
from ..core.logging_setup import log
from ..integration.tool_registry import IN_PROCESS_WINDOW_FACTORIES
from .settings.app_settings_dialog import AppSettingsDialog
from .widgets.log_panel import LogPanel
from .widgets.resource_bar import ResourceBar


class SettingsPage(QWidget):
    launch_requested = Signal(dict)
    open_running_requested = Signal()
    stop_requested = Signal()
    cancel_requested = Signal()
    language_changed = Signal(str)
    # НОВОЕ: Studio-wide выход/перезапуск, см. ui/settings/advanced_page.py
    # (кнопки "Application") и MainWindow.quit_studio()/restart_studio()
    # в launcher_window.py.
    quit_studio_requested = Signal()
    restart_studio_requested = Signal()

    def __init__(self, cfg, theme_manager: ThemeManager, loc=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.theme_manager = theme_manager
        self.loc = loc
        # Держит живые ссылки на окна остальных инструментов комплекта,
        # открытые В ЭТОМ ЖЕ процессе (см. IN_PROCESS_WINDOW_FACTORIES) --
        # без этого объект окна был бы собран сборщиком мусора Python сразу
        # после выхода из _launch_external() и окно бы тут же закрылось.
        #
        # ВАЖНО: раньше запись из этого словаря никогда не удалялась --
        # окно (а с ним и всё, что оно загрузило в память, для PromptVault
        # это модели torch/transformers) жило до самого закрытия ВСЕГО
        # приложения, даже если пользователь закрывал только окно
        # инструмента крестиком. См. _open_in_process_window() ниже --
        # там теперь окно реально уничтожается по закрытию и запись
        # удаляется из кэша, а не просто "скрывается" навсегда.
        self._child_windows = {}

        root = QVBoxLayout(self)

        # Полоса "ComfyUI уже запущен" — видна только пока процесс жив,
        # и именно тут разведены по смыслу кнопки "Настройки" и "Стоп":
        # эта страница сама по себе больше не останавливает сервер.
        self.running_bar = QWidget()
        running_row = QHBoxLayout(self.running_bar)
        running_row.setContentsMargins(0, 0, 0, 0)
        self.running_label = QLabel(self._tr("ComfyUI уже запущен"))
        self.running_label.setStyleSheet("color: #6fbf73; font-weight: bold;")
        running_row.addWidget(self.running_label)
        running_row.addStretch(1)
        self.open_running_btn = QPushButton(self._tr("Открыть ComfyUI"))
        self.open_running_btn.clicked.connect(self.open_running_requested.emit)
        running_row.addWidget(self.open_running_btn)
        self.stop_running_btn = QPushButton(self._tr("Остановить"))
        self.stop_running_btn.clicked.connect(self.stop_requested.emit)
        running_row.addWidget(self.stop_running_btn)
        root.addWidget(self.running_bar)
        self.running_bar.setVisible(False)

        # Единое дерево настроек (этап 4) -- построено сразу, а не лениво
        # по первому открытию: General-страница внутри должна быть готова
        # ловить theme_changed_externally/language_changed_externally с
        # самого старта приложения, даже если пользователь ни разу не
        # открывал "Settings..." — точно так же, как раньше были всегда
        # готовы theme_combo/language_combo прямо на этом экране.
        self.settings_dialog = AppSettingsDialog(
            cfg,
            theme_manager,
            loc,
            parent=self,
        )
        self.settings_dialog.language_changed.connect(self.language_changed.emit)
        self.settings_dialog.quit_studio_requested.connect(self.quit_studio_requested.emit)
        self.settings_dialog.restart_studio_requested.connect(self.restart_studio_requested.emit)

        settings_row = QHBoxLayout()
        self.settings_btn = QPushButton(self._tr("Настройки..."))
        self.settings_btn.clicked.connect(self._open_settings_dialog)
        settings_row.addWidget(self.settings_btn)
        settings_row.addStretch(1)
        root.addLayout(settings_row)

        # -- Другие инструменты комплекта: запускаются как отдельные,
        # независимые процессы (см. ExternalApp/launch_external_app выше).
        # Путь к ним не настраивается — оба поставляются в одном архиве
        # с лаунчером, в фиксированной подпапке tools/.
        self.tools_box = QGroupBox(self._tr("Другие инструменты"))
        tools_layout = QVBoxLayout(self.tools_box)
        self.external_status_labels = {}
        self.external_launch_btns = {}
        for app in EXTERNAL_APPS:
            row = QHBoxLayout()
            row.addWidget(QLabel(app.label))
            row.addStretch(1)

            launch_btn = QPushButton(self._tr("Запустить"))
            launch_btn.clicked.connect(
                lambda _checked=False, a=app: self._launch_external(a)
            )
            row.addWidget(launch_btn)
            self.external_launch_btns[app.subdir] = launch_btn

            tools_layout.addLayout(row)

            status_label = QLabel("")
            status_label.setWordWrap(True)
            self.external_status_labels[app.subdir] = status_label
            tools_layout.addWidget(status_label)

        root.addWidget(self.tools_box)
        self._refresh_external_status()

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #d9534f;")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.log_panel = LogPanel(loc)
        self.log_panel.setMinimumHeight(160)
        root.addWidget(self.log_panel, 1)

        self.resource_bar = ResourceBar(loc=self.loc)
        root.addWidget(self.resource_bar)

        # Полоса прогресса запуска — вместо отдельного экрана "Ожидание".
        # Остаёмся на экране настроек, чтобы был виден живой лог сверху.
        self.progress_row = QWidget()
        progress_layout = QHBoxLayout(self.progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        self.progress_status_label = QLabel("")
        progress_layout.addWidget(self.progress_status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedWidth(200)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addStretch(1)
        self.cancel_launch_btn = QPushButton(self._tr("Отмена"))
        self.cancel_launch_btn.clicked.connect(self.cancel_requested.emit)
        progress_layout.addWidget(self.cancel_launch_btn)
        root.addWidget(self.progress_row)
        self.progress_row.setVisible(False)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.launch_btn = QPushButton(self._tr("Запустить"))
        self.launch_btn.setDefault(True)
        self.launch_btn.clicked.connect(self._on_launch)
        btn_row.addWidget(self.launch_btn)
        root.addLayout(btn_row)

    # -- состояние "сервер уже запущен" --------------------------------

    def _open_settings_dialog(self):
        """Открывает единое дерево настроек НЕмодально (show()/raise(),
        а не exec()) -- это правка по замечанию пользователя: exec()
        делает диалог application-modal, из-за чего окно PromptVault,
        открытое кнопкой "Open PromptVault settings..." внутри этого же
        диалога (см. ui/settings/promptvault_page.py), оказывалось
        заблокировано и визуально пряталось ЗА модальным диалогом
        настроек лаунчера -- взаимодействовать с ним можно было, только
        закрыв диалог настроек. Немодальное окно ведёт себя так же, как
        и собственное окно настроек PromptVault (тоже show()/raise(),
        не exec()) -- оба могут быть открыты одновременно."""
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def set_server_running(self, running: bool, port=None):
        self.running_bar.setVisible(running)
        if running and port:
            self.running_label.setText(f"ComfyUI уже запущен на порту {port}")
        self.launch_btn.setEnabled(not running)
        self.settings_dialog.set_running_state(running)

    # -- прогресс запуска (вместо отдельной страницы) --------------------

    def show_launch_progress(self, text):
        self.progress_status_label.setText(text)
        self.progress_row.setVisible(True)
        self._set_launch_controls_enabled(False)

    def update_launch_progress(self, text):
        self.progress_status_label.setText(text)

    def hide_launch_progress(self):
        self.progress_row.setVisible(False)
        self._set_launch_controls_enabled(True)

    def _set_launch_controls_enabled(self, enabled):
        self.launch_btn.setEnabled(enabled)
        self.settings_dialog.set_running_state(not enabled)

    # -- прочее ------------------------------------------------------

    def set_status(self, text):
        self.status_label.setText(text)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        """Перевыставляет уже построенные тексты этой страницы (и её
        LogPanel/AppSettingsDialog) после смены языка — сам по себе выбор
        языка не обновляет текст уже созданных виджетов."""
        self.running_label.setText(self._tr("ComfyUI уже запущен"))
        self.open_running_btn.setText(self._tr("Открыть ComfyUI"))
        self.stop_running_btn.setText(self._tr("Остановить"))
        self.settings_btn.setText(self._tr("Настройки..."))
        self.settings_dialog.retranslate_ui()
        self.tools_box.setTitle(self._tr("Другие инструменты"))
        for btn in self.external_launch_btns.values():
            btn.setText(self._tr("Запустить"))
        self.launch_btn.setText(self._tr("Запустить"))
        self.cancel_launch_btn.setText(self._tr("Отмена"))
        self._refresh_external_status()
        self.log_panel.retranslate_ui()
        self.resource_bar.retranslate_ui()

    def _refresh_external_status(self):
        """Показывает, готово ли каждое приложение к запуску (и как именно
        оно будет запущено) — чисто информационно, путь не редактируется."""
        for app in EXTERNAL_APPS:
            status_label = self.external_status_labels[app.subdir]
            if app.subdir in IN_PROCESS_WINDOW_FACTORIES:
                # Монолитная сборка ComfyUIStudio: инструмент открывается
                # окном этого же процесса, отдельный exe/подпроцесс не
                # ищется и не нужен.
                status_label.setText(self._tr("Готово — откроется в этом же приложении."))
                status_label.setStyleSheet("color: #6fbf73;")
                continue
            cmd, cwd, error = resolve_external_launch(app)
            if error:
                status_label.setText(error)
                status_label.setStyleSheet("color: #d9534f;")
            else:
                status_label.setText(self._tr("Найдено: {}").format(cwd))
                status_label.setStyleSheet("color: #6fbf73;")

    def _launch_external(self, app: "ExternalApp"):
        status_label = self.external_status_labels[app.subdir]

        factory = IN_PROCESS_WINDOW_FACTORIES.get(app.subdir)
        if factory is not None:
            self._open_in_process_window(app, factory, status_label)
            return

        ok, message = launch_external_app(app)
        if ok:
            status_label.setText(
                self._tr("{} запущен в отдельном процессе.").format(app.label)
            )
            status_label.setStyleSheet("color: #6fbf73;")
        else:
            status_label.setText(message)
            status_label.setStyleSheet("color: #d9534f;")

    def _open_in_process_window(self, app: "ExternalApp", factory, status_label):
        """Открывает окно инструмента комплекта в текущем процессе (см.
        IN_PROCESS_WINDOW_FACTORIES/register_in_process_app выше). Если
        окно уже было открыто и просто свёрнуто/скрыто за другими окнами —
        поднимает существующее вместо создания второго.

        Когда пользователь ЗАКРЫВАЕТ окно (крестиком), оно не просто
        прячется: WA_DeleteOnClose заставляет Qt реально уничтожить его
        C++-объект после closeEvent, сигнал destroyed чистит запись в
        self._child_windows, а gc.collect() сразу забирает то, что окно
        держало в памяти (для PromptVault — загруженные модели
        torch/transformers). Раньше запись из кэша не удалялась никогда,
        и вся эта память оставалась занятой до закрытия всего приложения,
        даже если было закрыто только окно инструмента.
        """
        window = self._child_windows.get(app.subdir)
        if window is None:
            try:
                window = factory()
            except Exception as e:
                log.exception("Не удалось открыть окно %s", app.label)
                if status_label is not None:
                    status_label.setText(f"Не удалось открыть {app.label}: {e}")
                    status_label.setStyleSheet("color: #d9534f;")
                return
            window.setAttribute(Qt.WA_DeleteOnClose, True)
            window.destroyed.connect(
                lambda _obj=None, subdir=app.subdir: self._on_child_window_destroyed(subdir)
            )
            self._child_windows[app.subdir] = window

        window.show()
        window.raise_()
        window.activateWindow()

        if status_label is not None:
            status_label.setText(self._tr("{} открыт.").format(app.label))
            status_label.setStyleSheet("color: #6fbf73;")

    def _on_child_window_destroyed(self, subdir):
        self._child_windows.pop(subdir, None)
        # Явный gc.collect() — на объекте окна почти наверняка были
        # цикличные ссылки (сигналы/слоты, родитель/потомок в Qt), которые
        # обычный refcounting сам по себе не всегда убирает сразу же.
        gc.collect()
        log.info(
            "Окно инструмента '%s' закрыто и удалено из памяти процесса",
            subdir,
        )

    def _on_launch(self):
        # ВАЖНО: self.cfg тут -- это снимок на момент конструктора
        # (см. __init__), актуальные же значения после любых изменений в
        # единых настройках лежат в self.settings_dialog.cfg -- именно
        # AppSettingsDialog._auto_save() их обновляет (ComfyUISettingsPage/
        # AdvancedSettingsPage) и именно он остался единственным
        # "владельцем" конфигурации после этапа 4 (SettingsPage больше не
        # держит собственных полей формы, которые раньше читались
        # напрямую). Взять self.cfg здесь по ошибке означало бы запускать
        # ComfyUI с настройками на момент открытия приложения, игнорируя
        # любые правки, сделанные в "Настройки..." в текущем сеансе.
        cfg = self.settings_dialog.cfg
        root_path = cfg.get("root_path", "")
        ok, msg = validate_portable_root(root_path)
        if not ok:
            self.set_status(self._tr(msg))
            log.warning("Проверка папки не пройдена: %s", msg)
            return
        script = cfg.get("script", "")
        if not script:
            self.set_status(self._tr("Выберите скрипт запуска"))
            return

        self.set_status("")
        self.log_panel.text.clear()
        save_config(cfg)
        self.cfg = cfg
        self.launch_requested.emit(cfg)
