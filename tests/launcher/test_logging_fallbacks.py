"""Регрессия к «программа не запускается».

История правок 2026-10-03: на Windows запуск падал трейсбеком ещё до
создания окна —

    main.py -> log_memory("старт процесса, до QApplication")
            -> mem_diagnostics._ensure_configured()
            -> logging.FileHandler(...)
            PermissionError: [Errno 13] Permission denied:
                '...\\AppData\\Roaming\\ComfyUILauncher\\mem_diagnostics.log'

То есть диагностический файл (временный инструмент, см. докстринг
mem_diagnostics) был обязательным условием запуска всего комплекта:
любая причина недоступности одного файла — нет прав, файл занят другим
процессом, профиль только для чтения — превращалась в отсутствие окна.
Тот же дефект был в launcher/core/logging_setup.py (файловый хендлер
открывался на импорте модуля) и в логгере PromptVault.

Тесты ниже фиксируют контракт: недоступность файла лога деградирует до
консольного логирования, но НЕ бросает исключение.
"""

import logging

import pytest


def test_log_memory_does_not_raise_when_log_file_is_unavailable(monkeypatch):
    """log_memory() не должен падать, если файл диагностики открыть
    нельзя — именно этот вызов стоит в main.py до QApplication."""
    from comfyui_studio import mem_diagnostics

    class UnavailableFileHandler(logging.FileHandler):
        def __init__(self, *args, **kwargs):
            raise PermissionError(13, "Permission denied")

    logged = []
    monkeypatch.setattr(logging, "FileHandler", UnavailableFileHandler)
    monkeypatch.setattr(mem_diagnostics, "_configured", False)
    monkeypatch.setattr(
        mem_diagnostics._logger,
        "warning",
        lambda *args, **kwargs: logged.append(args),
    )

    # Ни одного исключения наружу -- это и есть проверяемое поведение.
    mem_diagnostics.log_memory("тест: файл диагностики недоступен")

    assert mem_diagnostics._configured is True, (
        "неудачная попытка открыть файл не должна оставлять модуль "
        "неконфигурированным: иначе каждая следующая контрольная точка "
        "будет повторять ту же попытку"
    )
    assert any("недоступен" in str(args[0]) for args in logged), (
        "о деградации до консоли нужно предупредить"
    )


def test_launcher_setup_logging_does_not_raise_when_file_is_unavailable(monkeypatch):
    """То же для логгера лаунчера (файл launcher.log рядом с тем же
    каталогом): раньше он открывался прямо на импорте модуля."""
    from comfyui_studio.launcher.core import logging_setup

    class UnavailableRotatingFileHandler(logging.Handler):
        def __init__(self, *args, **kwargs):
            raise PermissionError(13, "Permission denied")

        def emit(self, record):  # pragma: no cover -- не вызывается
            pass

    monkeypatch.setattr(logging_setup, "RotatingFileHandler", UnavailableRotatingFileHandler)
    # Возвращаем модулю исходное значение после теста (setup_logging()
    # перезапишет его).
    monkeypatch.setattr(logging_setup, "ACTIVE_LOG_PATH", logging_setup.ACTIVE_LOG_PATH)

    logger = logging_setup.setup_logging()

    # Проверяем не «сколько хендлеров», а что до файла дело не дошло и
    # консольный хендлер всё равно есть.
    assert logger is not None
    assert any(
        isinstance(handler, logging.StreamHandler) for handler in logger.handlers
    ), "без файлового лога консольный хендлер обязан остаться"
    # Именно по этому признаку интерфейс показывает предупреждение о
    # недоступной папке данных (см. launcher_window._warn_if_data_dir_unwritable).
    assert logging_setup.ACTIVE_LOG_PATH is None
    assert logging_setup.file_log_available() is False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
