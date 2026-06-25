@echo off
REM Build a standalone tidysync.exe into this folder (BUILD ONLY).
REM One-time setup:  pip install -r requirements.txt pyinstaller
cd /d "%~dp0"
python -m PyInstaller --onefile --console --name tidysync ^
  --paths src --collect-submodules tidysync ^
  --distpath . --workpath build\pyi --specpath build app.py
if errorlevel 1 (
  echo.
  echo Build FAILED.
  exit /b 1
)
echo.
echo Built: %~dp0tidysync.exe
echo Test:  tidysync.exe --version
