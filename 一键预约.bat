@echo off
cd /d "%~dp0"
python app_entry.py
if errorlevel 1 pause
