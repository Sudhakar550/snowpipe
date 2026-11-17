@echo off
REM Change the password below before sharing the page.
cd /d "%~dp0"
set UPLOAD_USERS=dialog:change-me
set PORT=8000
python -m pip install -q -r requirements.txt
python app.py
pause
