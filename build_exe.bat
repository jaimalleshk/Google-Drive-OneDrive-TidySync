@echo off
REM Build a standalone tidysync.exe into this folder.
REM Requires: pip install pyinstaller   (one time)
cd /d "%~dp0"
pyinstaller --onefile --console --name tidysync ^
  --paths src --collect-submodules tidysync ^
  --distpath . --workpath build\pyi --specpath build app.py
echo.
echo Built: %~dp0tidysync.exe
echo Test:  tidysync.exe --version
