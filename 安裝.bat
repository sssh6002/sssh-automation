@echo off
rem ===========================================================
rem  ASCII ONLY - DO NOT PUT CHINESE IN THIS FILE.
rem  cmd.exe reads .bat files with the system ANSI codepage
rem  (Big5/cp950 here), not UTF-8. Chinese text here corrupts
rem  the whole file: "@echo off" got eaten into "cho" and the
rem  script would not run at all (tested 2026-08-12).
rem  All Chinese messages live in install.py, which Python
rem  prints correctly after chcp 65001 below.
rem ===========================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem This .bat is only a switch - the work is done by the files next to it.
rem Copying this single file to another PC will NOT work (tested 2026-08-12:
rem you get "can't open file install.py", which means nothing to a layperson).
if not exist "install.py" goto notfolder
if not exist "ui.py" goto notfolder

set PYEXE=
call :findpy python
if not defined PYEXE call :findpy py
if not defined PYEXE goto nopython

"%PYEXE%" install.py
set RC=%ERRORLEVEL%
echo.
pause
exit /b %RC%

:notfolder
echo.
echo  [X] This file cannot run on its own.
echo.
echo      install.py / ui.py are missing, so this is not the tool folder.
echo      You must copy the WHOLE folder to the other PC, not just this
echo      one .bat file.
echo.
echo      Ask whoever gave you this file for the whole folder, or for
echo      the handover zip built by "handover.py". The Chinese setup
echo      guide is the .md file inside that folder.
echo.
pause
exit /b 1

:nopython
echo.
echo  [X] Python not found. This tool needs Python 3.10 or newer.
echo.
rem winget ships with Windows 10/11 (App Installer). If it is here we can
rem install Python without sending the user to a download page.
rem ASK FIRST - never install anything on someone's PC unprompted.
rem Default is NO: this window may be running without a keyboard attached
rem (piped/automated), and a blank answer must not trigger an install.
where winget >nul 2>nul
if errorlevel 1 goto manualpy
echo      This PC has "winget", so it can be installed for you.
echo.
set ANS=N
set /p ANS="      Install Python now with winget? (Y/N, default N): "
if /i not "%ANS%"=="Y" goto manualpy
echo.
echo      Installing Python (per-user, no admin needed)...
winget install --id Python.Python.3.13 --exact --scope user ^
  --accept-package-agreements --accept-source-agreements
echo.
echo      ------------------------------------------------------------
echo      If it installed OK: CLOSE this window and run this file
echo      again. Windows only picks up the new PATH in a new window.
echo      ------------------------------------------------------------
echo.
pause
exit /b 1

:manualpy
echo.
echo      Install it yourself:
echo        1. Go to https://python.org/downloads
echo        2. Download Python 3.13 for Windows
echo        3. On the FIRST setup screen, tick "Add python.exe to PATH"
echo        4. Finish, then run this file again
echo.
pause
exit /b 1

rem --- ask a command where its python.exe lives ---------------
:findpy
"%~1" -c "import sys;print(sys.executable)" > "%TEMP%\sssh_py.txt" 2>nul
if errorlevel 1 goto :eof
set /p PYEXE=<"%TEMP%\sssh_py.txt"
del "%TEMP%\sssh_py.txt" >nul 2>nul
goto :eof
