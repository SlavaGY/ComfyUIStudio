"""ComfyUI Character/Prompt Builder Config Editor — пакет.

Наличие этого файла превращает tools/prompt_builder в обычный Python-пакет
(`prompt_builder`), а не набор изолированных модулей. Это позволяет
статическому анализатору импортов PyInstaller (см. корневой main.py и
ComfyUIStudio.spec) обнаружить и скомпилировать весь код инструмента в
PYZ-архив вместе с остальной сборкой — по тому же принципу, что и пакет
`app` у PromptVault (tools/promptvault/app/__init__.py), — вместо того
чтобы копировать исходники .py как есть в _internal/tools/prompt_builder
и подгружать их в рантайме через importlib.
"""
