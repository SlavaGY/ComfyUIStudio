"""Общие fixtures для тестов, использующих реальные Qt-виджеты.

Тесты, которым нужен QApplication, должны запускаться с
QT_QPA_PLATFORM=offscreen (см. CONTRIBUTING.md):

    QT_QPA_PLATFORM=offscreen pytest
"""

# Фикстура `qapp` больше не определяется здесь вручную — её предоставляет
# pytest-qt (см. requirements-dev.txt / pyproject.toml). Она даёт тот же
# единственный на сессию QApplication, но дополнительно снимает с нас
# обязанность вручную поддерживать qapp_args/qapp_cls и т.п.; qtbot,
# который тоже приносит pytest-qt, тестами пока не используется, но
# доступен на будущее.
