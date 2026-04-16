@echo off
cd /d "%~dp0"
echo 🚀 Iniciando Bot Autocontador...
".venv_312\Scripts\python.exe" bot.py
if %ERRORLEVEL% neq 0 (
    echo ❌ Ocurrio un error al iniciar el bot.
    pause
)
