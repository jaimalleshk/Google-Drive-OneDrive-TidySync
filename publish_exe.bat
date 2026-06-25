@echo off
REM MAINTAINER ONLY: rebuild tidysync.exe, then refresh the committed + released copy.
REM (Downloaders building their own copy should use build_exe.bat instead.)
setlocal
cd /d "%~dp0"

call build_exe.bat
if errorlevel 1 exit /b 1

where git >nul 2>nul
if errorlevel 1 (
  echo [skip] git not found - exe built but not committed.
  goto :eof
)

git diff --quiet -- tidysync.exe
if errorlevel 1 (
  echo === Committing + pushing refreshed exe ===
  git add tidysync.exe
  git commit -m "build: refresh prebuilt tidysync.exe"
  git push
) else (
  echo tidysync.exe is unchanged since last commit - nothing to push.
)

where gh >nul 2>nul
if errorlevel 1 (
  echo [skip] gh CLI not found - GitHub Release asset not updated.
  goto :eof
)

set "REL="
for /f "delims=" %%T in ('gh release list --limit 1 --json tagName --jq ".[0].tagName" 2^>nul') do set "REL=%%T"
if defined REL (
  echo === Updating Release %REL% asset ===
  gh release upload %REL% tidysync.exe --clobber
) else (
  echo [skip] no GitHub Release found - run: gh release create vX.Y.Z tidysync.exe
)

echo Done.
endlocal
