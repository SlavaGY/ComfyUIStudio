@echo off
REM Сборка PromptConfigEditor.exe (один файл, без консоли).
REM Запускать в папке проекта, из активированного venv (см. README).

pip install -r requirements.txt
pip install pyinstaller

pyinstaller build.spec --noconfirm

echo.
echo Готово: dist\PromptConfigEditor.exe
pause
