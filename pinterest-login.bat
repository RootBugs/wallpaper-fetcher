@echo off
title Pinterest Login (ek hi baar karna hai)
cd /d "%~dp0"
echo ============================================
echo   Pinterest login - SIRF EK BAAR
echo   Session .pin_profile me save ho jayega
echo ============================================
echo.
python pin_home_feed.py --urls https://www.pinterest.com/ --wait-login 300
echo.
echo Agar "login detected" dikha to ho gaya! Band kar sakte ho.
pause
