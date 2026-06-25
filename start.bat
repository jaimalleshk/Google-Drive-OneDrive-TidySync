@echo off
REM Double-click this to open the tidysync menu.
cd /d "%~dp0"
python -m tidysync %*
pause
