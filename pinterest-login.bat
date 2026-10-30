@echo off
title Pinterest Login (run once)
cd /d "%~dp0"
echo ============================================
echo   Pinterest login - RUN ONCE ONLY
echo   The session is saved into .pin_profile
echo ============================================
echo.
python pin_home_feed.py --urls https://www.pinterest.com/ --wait-login 300
echo.
echo If it said "login detected" you are done - you can close this.
pause
