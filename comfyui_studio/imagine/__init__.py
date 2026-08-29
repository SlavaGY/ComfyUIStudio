"""
Imagine — локальный генератор поверх ComfyUI (FastAPI + ванильный JS),
встроенный в комплект ComfyUIStudio как альтернативный способ открыть
ComfyUI (см. launcher/core/imagine_process.py и раздел "Интерфейс" в
ui/settings/comfyui_page.py).

Раньше был отдельным, не встроенным в Studio инструментом (см. историю
в README самого imagine-app) — сам backend/launcher.py уже тогда
отмечал, что при интеграции в Studio будет использоваться существующий
comfy_process.py вместо его собственного упрощённого запуска ComfyUI.
Здесь это и сделано: ComfyUIStudio запускает ComfyUI как обычно (через
ComfyProcess) и передаёт Imagine его host/port явно при старте, вместо
того чтобы Imagine поднимал ComfyUI сам.
"""
