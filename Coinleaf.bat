@echo off
rem Windows: double-click to open Coinleaf in your browser.
rem The app runs only on this computer; close this window (or press Ctrl+C) to stop it.
setlocal
cd /d "%~dp0"
set "UV=uv"
where uv >nul 2>nul || set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not "%UV%"=="uv" if not exist "%UV%" (
  echo uv is not installed. Open PowerShell and run:
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  echo then double-click this file again. See README.md, Setup.
  pause
  exit /b 1
)
"%UV%" sync -q
if errorlevel 1 (
  pause
  exit /b 1
)
"%UV%" run coinleaf
pause
