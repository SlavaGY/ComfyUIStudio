"""Аппаратные метрики ResourceMonitor: CPU, RAM и GPU.

_init_hardware вызывается один раз при создании монитора; _poll получает
снимки через _collect_hardware_stats. Широкие обработчики с логированием
изолируют ошибки аппаратных библиотек и драйверов."""

from __future__ import annotations

from .logging_setup import log
from .system_monitor_base import ResourceMonitorBase

try:
    import psutil
except ImportError:
    psutil = None

try:
    import pynvml
except ImportError:
    pynvml = None


class HardwareMixin(ResourceMonitorBase):
    def _init_hardware(self) -> None:
        """Инициализация NVML и первый (прогревочный) вызов psutil. Вызывается
        один раз из ResourceMonitor.__init__."""

        if pynvml is not None:
            try:
                pynvml.nvmlInit()
                self._gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                self._nvml_ok = True
                log.info("NVML инициализирован, GPU-метрики доступны")
            except Exception as e:
                log.warning("NVML недоступен (нет NVIDIA GPU или драйвера?): %s", e)
        else:
            log.warning("Модуль pynvml не установлен — метрики GPU будут недоступны")

        if psutil is not None:
            psutil.cpu_percent(interval=None)  # первый вызов всегда возвращает 0.0
        else:
            log.warning("Модуль psutil не установлен — метрики CPU/RAM будут недоступны")

    def _collect_hardware_stats(self):
        stats = {}

        if psutil is not None:
            try:
                stats["cpu_percent"] = psutil.cpu_percent(interval=None)
                vm = psutil.virtual_memory()
                stats["ram_percent"] = vm.percent
                stats["ram_used_gb"] = vm.used / (1024 ** 3)
                stats["ram_total_gb"] = vm.total / (1024 ** 3)
            except Exception:
                if not self._warned_psutil:
                    log.exception("Ошибка чтения метрик CPU/RAM")
                    self._warned_psutil = True

        if self._nvml_ok:
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(self._gpu_handle)
                mem = pynvml.nvmlDeviceGetMemoryInfo(self._gpu_handle)
                temp = pynvml.nvmlDeviceGetTemperature(
                    self._gpu_handle, pynvml.NVML_TEMPERATURE_GPU
                )
                name = pynvml.nvmlDeviceGetName(self._gpu_handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", "ignore")
                stats["gpu_available"] = True
                stats["gpu_name"] = name
                stats["gpu_util"] = util.gpu
                stats["gpu_mem_used_gb"] = mem.used / (1024 ** 3)
                stats["gpu_mem_total_gb"] = mem.total / (1024 ** 3)
                stats["gpu_temp"] = temp
            except Exception:
                if not self._warned_nvml:
                    log.exception("Ошибка чтения метрик GPU")
                    self._warned_nvml = True
                stats["gpu_available"] = False
        else:
            stats["gpu_available"] = False
        return stats
