# Версия Python-части ComfyUI Studio (Launcher + Prompt Builder +
# PromptVault + Remote) -- ЕДИНСТВЕННЫЙ источник. Остальные места читают
# её отсюда: pyproject.toml (dynamic version, attr -- setuptools разбирает
# эту строку статически, не импортируя пакет, поэтому ниже должен остаться
# простой строковый литерал), окно настроек (ui/settings/general_page.py),
# Remote API и promptvault/config.py (APP_VERSION). Правится только здесь;
# страж -- tests/test_version_single_source.py. Android версионируется
# отдельно.
__version__ = "0.1.0"
