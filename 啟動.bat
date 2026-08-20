@echo off
rem ===========================================================
rem  ASCII ONLY - DO NOT PUT CHINESE IN THIS FILE, not even in a
rem  comment, not even a filename. cmd reads .bat with the system
rem  ANSI codepage (Big5 here), so any Chinese byte can swallow the
rem  next character and break parsing. Tested the hard way on
rem  2026-08-14: two "rem"/"echo" lines mentioning a Chinese .bat
rem  filename made double-clicking this file do nothing at all.
rem  tests/test_installer.py now fails if non-ASCII creeps back in.
rem  Chinese messages are printed by ui.py itself.
rem
rem  Uses the interpreter recorded by install.py. This machine
rem  has two Pythons and only one has openpyxl; ui.py launches
rem  every sub-task with the interpreter that started it, so the
rem  whole flow must use the same one.
rem ===========================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set PYEXE=
if exist "python-path.txt" set /p PYEXE=<"python-path.txt"
if not defined PYEXE set PYEXE=python

"%PYEXE%" ui.py
set RC=%ERRORLEVEL%

if not "%RC%"=="0" (
  echo.
  echo  Exit code %RC%. Run the setup .bat again to re-check.
  echo.
  pause
)
exit /b %RC%
