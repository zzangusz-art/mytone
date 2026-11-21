@echo off
chcp 65001 >nul
setlocal
rem 내말투 상담 - 실행. 창을 닫으면 서버가 꺼집니다.
set "VENV=%LOCALAPPDATA%\MyTone\venv"
if not exist "%VENV%\Scripts\python.exe" (
  echo 먼저 0_SETUP.bat 을 실행해 주세요.
  pause
  exit /b 1
)
cd /d "%~dp0"
rem 서버가 뜰 시간을 3초 준 뒤 브라우저를 엽니다
start "" /min cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8700"
"%VENV%\Scripts\python.exe" -X utf8 -m app.main %*
pause
