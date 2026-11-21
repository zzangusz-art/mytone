@echo off
chcp 65001 >nul
setlocal
rem 내말투 상담 - 자체 점검. 임시 폴더에서 돌리므로 실제 데이터는 건드리지 않습니다.
set "VENV=%LOCALAPPDATA%\MyTone\venv"
cd /d "%~dp0"
"%VENV%\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements-dev.txt
if not exist "samples\*.m4a" "%VENV%\Scripts\python.exe" -X utf8 scripts\make_sample_calls.py
"%VENV%\Scripts\python.exe" -X utf8 scripts\selftest.py
pause
