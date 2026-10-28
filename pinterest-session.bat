@echo off
title Pinterest Session - one time setup
cd /d "%~dp0"
color 0A
echo ==================================================
echo    PINTEREST SESSION  (sirf EK BAAR karna hai)
echo    Chrome se tumhara login session utha lega
echo ==================================================
echo.
echo  STEP 1:  Apna CHROME BAND karo
echo           (saare Chrome windows close kar lo)
echo.
echo  STEP 2:  Wahan ENTER dabao
echo.
pause > nul
echo.
echo  [1/2] Chrome cookies padhi ja rahi hain...
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
echo    DONE  -  session save ho gaya  (pin_session.json)
echo.
echo    Ab Chrome wapas khol lo.
echo    Tool apne aap ye session use karega - ab har
echo    fetch me ASLI Pinterest home feed aayega.
echo ==================================================
start "" "chrome"
pause
exit /b 0

:LOCKED
echo ==================================================
echo    PROBLEM: Chrome ABHI BHI chal raha hai.
echo.
echo    1. Saare Chrome windows band karo (taskbar se)
echo    2. Ye bat dobara double-click karo
echo ==================================================
pause
exit /b 2

:NOLOGIN
echo ==================================================
echo    PROBLEM: Pinterest ki koi cookie nahi mili.
echo    Matlab Chrome me Pinterest me LOGIN nahi ho.
echo.
echo    -> Chrome me pinterest.com kholo, login karo,
echo       phir ye bat dobara chalao.
echo ==================================================
start "" "chrome"
pause
exit /b 3

:NOSESSION
echo ==================================================
echo    PROBLEM: _pinterest_sess / _auth nahi mili.
echo    Chrome me Pinterest pe login karke dobara chalao.
echo ==================================================
start "" "chrome"
pause
exit /b 4

:OTHER
echo ==================================================
echo    Nahi hua (exit code %ERR%).
echo.
echo    Agar "V20_ABE" likha hai to Chrome nahi cookies
echo    ko app-bound encryption se lock kar diya hai -
echo    tab bata do, main alag rasta (seedha Chrome se
echo    chalane wala) bana doonga.
echo.
echo    Agar "LOCKED" likha hai to Chrome abhi chal raha hai.
echo ==================================================
pause
exit /b 1
