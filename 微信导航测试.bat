@echo off
cd /d "%~dp0"
python app_entry.py --login
if errorlevel 1 pause
