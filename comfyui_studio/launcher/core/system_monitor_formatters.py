"""Форматирование метрик для панели ресурсов и системного трея."""

from .constants import APP_NAME


def _identity(text):
    return text

def format_eta_seconds(seconds, tr=None) -> str:
    """Форматирует ETA для панели очереди и подсказки трея.

    None означает неизвестную скорость. Без функции перевода tr подписи
    выводятся на русском языке."""
    if tr is None:
        if tr is None:
            tr = _identity
    if seconds is None:
        return tr("оценка...")
    if seconds < 1:
        return tr("< 1 с")
    m, s = divmod(int(round(seconds)), 60)
    return f"~{m} {tr('мин')} {s} {tr('с')}" if m > 0 else f"~{s} {tr('с')}"



def format_stats_tooltip(stats: dict, tr=None) -> str:
    """Компактная подсказка для трея.

    Важно: Windows обрезает текст всплывающей подсказки трея примерно
    на 128 символах без предупреждения (именно так "срезалась" строка
    с очередью). Поэтому здесь без заголовка, без лишних слов и с
    жёсткой подстраховкой по длине.

    tr -- необязательная функция перевода, см. format_eta_seconds().
    """
    if tr is None:
        if tr is None:
            tr = _identity
    lines = []
    if "cpu_percent" in stats:
        lines.append(f"CPU {stats['cpu_percent']:.0f}%")
    if "ram_percent" in stats:
        gb = tr("ГБ")
        lines.append(
            f"RAM {stats['ram_percent']:.0f}% "
            f"({stats['ram_used_gb']:.1f}/{stats['ram_total_gb']:.1f} {gb})"
        )
    if stats.get("gpu_available"):
        lines.append(f"GPU {stats['gpu_util']}% {stats['gpu_temp']}°C")
        gb = tr("ГБ")
        lines.append(
            f"VRAM {stats['gpu_mem_used_gb']:.1f}/{stats['gpu_mem_total_gb']:.1f} {gb}"
        )
    if "queue_pending" in stats:
        running, pending = stats["queue_running"], stats["queue_pending"]
        line = f"{tr('Очередь')} {running}/{pending}"
        if "queue_completed_session" in stats:
            line += f" · {tr('Готово')} {stats['queue_completed_session']}"
        if (running + pending) > 0 and "queue_eta_seconds" in stats:
            line += f" · ETA {format_eta_seconds(stats['queue_eta_seconds'], tr=tr)}"
        lines.append(line)
    else:
        lines.append(tr("ComfyUI не запущен"))

    text = "\n".join(lines) if lines else APP_NAME
    if len(text) > 120:
        text = text[:117] + "..."
    return text



def level_color(value, warn=60, crit=85):
    """Цвет по уровню нагрузки/температуры: зелёный -> жёлтый -> красный."""
    if value is None:
        return "#5b6472"
    if value >= crit:
        return "#d9534f"
    if value >= warn:
        return "#d98c2b"
    return "#3fae4f"


NEUTRAL_CHIP_COLOR = "#5b6472"
QUEUE_ACTIVE_COLOR = "#3a7ecf"
QUEUE_IDLE_COLOR = "#3fae4f"
