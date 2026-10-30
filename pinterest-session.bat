@echo off
title Pinterest Session - one time setup
cd /d "%~dp0"
color 0A
echo ==================================================
echo    PINTEREST SESSION  (run this only ONCE)
echo    Picks up your Chrome login session
echo ==================================================
echo.
echo  STEP 1:  CLOSE Chrome
echo           (close all Chrome windows)
echo.
echo  STEP 2:  Press ENTER below
echo.
pause > nul
echo.
echo  [1/2] Reading Chrome cookies...
echo.
python chrome_pinterest_session.py
set ERR=%ERRORLEVEL%
echo.
if "%ERR%"=="0" goto OK
if "%ERR%"=="2" goto LOCKED
if "%ERR%"=="3" goto NOLOGIN
if "%ERR%"=="4" goto NOSESSION
goto OTHER

:OK
echo ==================================================
echo    DONE  -  session saved  (pin_session.json)
echo.
echo    You can open Chrome again.
echo    The tool will use this session automatically, so
echo    every fetch now sees your REAL Pinterest home feed.
echo ==================================================
start "" "chrome"
pause
exit /b 0

:LOCKED
echo ==================================================
echo    PROBLEM: Chrome is STILL running.
echo.
echo    1. Close every Chrome window (from the taskbar)
echo    2. Double-click this file again
echo ==================================================
pause
exit /b 2

:NOLOGIN
echo ==================================================
echo    PROBLEM: No Pinterest cookies found.
echo    That means you are not logged in to Pinterest in Chrome.
echo.
echo    -> Open pinterest.com in Chrome, log in,
echo       then run this file again.
echo ==================================================
start "" "chrome"
pause
exit /b 3

:NOSESSION
echo ==================================================
echo    PROBLEM: _pinterest_sess / _auth not found.
echo    Log in to Pinterest in Chrome, then run this again.
echo ==================================================
start "" "chrome"
pause
exit /b 4

:OTHER
echo ==================================================
echo    Failed (exit code %ERR%).
echo.
echo    If you see "V20_ABE", Chrome has locked its cookies
echo    with App-Bound Encryption - they cannot be decrypted
echo    from outside. Tell me and I will add the fallback
echo    path (driving your installed Chrome directly).
echo.
echo    If you see "LOCKED", Chrome is still running.
echo ==================================================
pause
exit /b 1
