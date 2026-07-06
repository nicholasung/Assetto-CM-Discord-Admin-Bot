@echo off
rem Launches the bot from wherever this file lives — double-click it, put a
rem shortcut to it in shell:startup, or point a Task Scheduler action at it.
setlocal
cd /d "%~dp0"

rem If you haven't set ACBOT_DISCORD_TOKEN persistently with `setx` (see
rem README.md), you can instead uncomment the line below and fill in your
rem token. Anyone with file access on this VM could then read it, so setx
rem is the safer option.
rem set ACBOT_DISCORD_TOKEN=your-bot-token-here

.venv\Scripts\python.exe -m acbot run
if errorlevel 1 (
    echo.
    echo acbot exited with an error - see data\logs\acbot.log for details.
    pause
)
