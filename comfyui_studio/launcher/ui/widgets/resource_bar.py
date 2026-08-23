"""
Виджет-полоска с "чипами" CPU/RAM/GPU/VRAM/очередь.

Вынесено из comfyui_launcher.py (этап 1 дорожной карты).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ...core.system_monitor import (
    NEUTRAL_CHIP_COLOR,
    QUEUE_ACTIVE_COLOR,
    QUEUE_IDLE_COLOR,
    format_eta_seconds,
    level_color,
)


class ResourceBar(QWidget):
    """Цветные "чипы" CPU/RAM/GPU/VRAM/очередь — цвет отражает уровень
    нагрузки (зелёный/жёлтый/красный), а не тему оформления, чтобы это
    было заметно на любой теме."""

    def __init__(self, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc
        self._last_stats = {}
        self._last_node_info = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.cpu_chip = self._make_chip()
        self.ram_chip = self._make_chip()
        self.gpu_chip = self._make_chip()
        self.vram_chip = self._make_chip()
        self.queue_chip = self._make_chip()
        self.node_chip = self._make_chip()
        self.node_chip.setVisible(False)  # виден только пока что-то реально выполняется

        for chip in (
            self.cpu_chip,
            self.ram_chip,
            self.gpu_chip,
            self.vram_chip,
            self.queue_chip,
            self.node_chip,
        ):
            layout.addWidget(chip)
        layout.addStretch(1)

        self.update_stats({})

    def _make_chip(self):
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignCenter)
        self._style_chip(lbl, NEUTRAL_CHIP_COLOR)
        return lbl

    @staticmethod
    def _style_chip(label, color):
        label.setStyleSheet(
            f"QLabel {{ background-color: {color}; color: #ffffff; "
            "border-radius: 9px; padding: 3px 10px; font-weight: 600; }"
        )

    def update_stats(self, stats: dict):
        self._last_stats = stats
        if "cpu_percent" in stats:
            v = stats["cpu_percent"]
            self.cpu_chip.setText(f"CPU {v:.0f}%")
            self._style_chip(self.cpu_chip, level_color(v))
        else:
            self.cpu_chip.setText(f"CPU: {self._tr('н/д')}")
            self._style_chip(self.cpu_chip, NEUTRAL_CHIP_COLOR)

        if "ram_percent" in stats:
            v = stats["ram_percent"]
            gb = self._tr("ГБ")
            self.ram_chip.setText(
                f"RAM {v:.0f}% ({stats['ram_used_gb']:.1f}/{stats['ram_total_gb']:.1f} {gb})"
            )
            self._style_chip(self.ram_chip, level_color(v))
        else:
            self.ram_chip.setText(f"RAM: {self._tr('н/д')}")
            self._style_chip(self.ram_chip, NEUTRAL_CHIP_COLOR)

        if stats.get("gpu_available"):
            self.gpu_chip.setText(f"GPU {stats['gpu_util']}%")
            self._style_chip(self.gpu_chip, level_color(stats["gpu_util"]))
            gb = self._tr("ГБ")
            self.vram_chip.setText(
                f"{stats['gpu_temp']}°C · VRAM "
                f"{stats['gpu_mem_used_gb']:.1f}/{stats['gpu_mem_total_gb']:.1f} {gb}"
            )
            self._style_chip(
                self.vram_chip, level_color(stats["gpu_temp"], warn=70, crit=84)
            )
        else:
            self.gpu_chip.setText(f"GPU: {self._tr('н/д')}")
            self._style_chip(self.gpu_chip, NEUTRAL_CHIP_COLOR)
            self.vram_chip.setText(f"VRAM: {self._tr('н/д')}")
            self._style_chip(self.vram_chip, NEUTRAL_CHIP_COLOR)

        if "queue_pending" in stats:
            running, pending = stats["queue_running"], stats["queue_pending"]
            text = f"{self._tr('Очередь')} {running}/{pending}"
            if "queue_completed_session" in stats:
                text += f" · {self._tr('Готово')} {stats['queue_completed_session']}"
            active = (running + pending) > 0
            if active and "queue_eta_seconds" in stats:
                eta = format_eta_seconds(stats['queue_eta_seconds'], tr=self._tr)
                text += f" · ETA {eta}"
            self.queue_chip.setText(text)
            self._style_chip(
                self.queue_chip, QUEUE_ACTIVE_COLOR if active else QUEUE_IDLE_COLOR
            )
        else:
            self.queue_chip.setText(self._tr("ComfyUI не запущен"))
            self._style_chip(self.queue_chip, NEUTRAL_CHIP_COLOR)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def set_executing_node(self, node_info):
        """Вызывается из MainWindow при ResourceMonitor.node_execution_changed
        (этап 8, последний пункт -- "текущий workflow, выполняемая
        нода"). node_info -- {"node","display_node","prompt_id"} или
        None (сейчас ничего не выполняется -- чип скрывается, а не
        показывает пустое значение, в отличие от остальных чипов,
        которые всегда что-то показывают)."""
        self._last_node_info = node_info
        if node_info is None:
            self.node_chip.setVisible(False)
            return
        label = node_info.get("display_node") or node_info.get("node") or "?"
        self.node_chip.setText(f"\u25B6 {self._tr('Нода')}: {label}")
        self._style_chip(self.node_chip, QUEUE_ACTIVE_COLOR)
        self.node_chip.setVisible(True)

    def retranslate_ui(self):
        self.update_stats(self._last_stats)
        self.set_executing_node(self._last_node_info)


# --------------------------------------------------------------------------
# Небольшая панель лога, переиспользуемая на странице настроек
# --------------------------------------------------------------------------


