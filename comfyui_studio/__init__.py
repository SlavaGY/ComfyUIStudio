# Версия всего комплекта ComfyUI Studio (Launcher + Prompt Builder +
# PromptVault) -- держится в одном месте, а не по одной в каждом
# инструменте (у PromptVault, впрочем, остаётся собственная APP_VERSION
# в comfyui_studio/promptvault/config.py -- это версия самого
# PromptVault как отдельно когда-то распространявшегося инструмента,
# менять её смысл в объём этого этапа не входит).
#
# Используется в ui/settings/general_page.py (раздел "Updates") и
# должна совпадать с version в корневом pyproject.toml -- пока нет
# автоматической синхронизации между ними (см. этап 5 дорожной карты).
__version__ = "0.1.0"
