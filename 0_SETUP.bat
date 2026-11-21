@echo off
chcp 65001 >nul
setlocal
rem 내말투 상담 - 설치. 여러 번 실행해도 안전합니다. 이미 있는 것은 건너뜁니다.
set "HOME_DIR=%LOCALAPPDATA%\MyTone"
set "VENV=%HOME_DIR%\venv"
set "MODELS=%HOME_DIR%\models"
set "MODEL=ggml-large-v3-turbo-q5_0.bin"
set "MODEL_URL=https://huggingface.co/ggerganov/whisper.cpp/resolve/main/%MODEL%"

echo [1/4] Python 확인
where python >nul 2>nul
if errorlevel 1 (
  echo   Python 이 없습니다. python.org 에서 3.10 이상을 설치한 뒤 다시 실행해 주세요.
  goto :fail
)

echo [2/4] ffmpeg 확인 - 음성 인식에 씁니다
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo   ffmpeg 이 없습니다. 명령 프롬프트에서  winget install Gyan.FFmpeg  실행 후 다시 시도해 주세요.
  goto :fail
)
ffmpeg -hide_banner -filters 2>nul | findstr /C:" whisper " >nul
if errorlevel 1 (
  echo   이 ffmpeg 에는 whisper 필터가 없습니다. ffmpeg 8 이상 full build 가 필요합니다.
  goto :fail
)

echo [3/4] 가상환경과 패키지 설치 - %VENV%
if not exist "%VENV%\Scripts\python.exe" python -m venv "%VENV%"
if not exist "%VENV%\Scripts\python.exe" goto :fail
"%VENV%\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r "%~dp0requirements.txt"
if errorlevel 1 goto :fail
"%VENV%\Scripts\python.exe" -X utf8 -c "import fastapi, anthropic, multipart, uvicorn"
if errorlevel 1 (
  echo   패키지를 불러오지 못했습니다. README 의 "앱 제어 정책" 항목을 확인해 주세요.
  goto :fail
)

echo [4/4] 음성 인식 모델 - 약 570MB, 처음 한 번만 받습니다
if not exist "%MODELS%" mkdir "%MODELS%"
if not exist "%MODELS%\%MODEL%" (
  curl -L --fail -o "%MODELS%\%MODEL%.part" "%MODEL_URL%"
  if not errorlevel 1 move /y "%MODELS%\%MODEL%.part" "%MODELS%\%MODEL%" >nul
)
if not exist "%MODELS%\%MODEL%" (
  echo   모델을 받지 못했습니다. 인터넷 연결을 확인해 주세요.
  goto :fail
)

echo.
echo 설치 완료. 1_RUN.bat 을 실행하세요.
if not "%1"=="--nopause" pause
exit /b 0

:fail
echo.
echo 설치를 끝내지 못했습니다. 위 메시지를 확인해 주세요.
if not "%1"=="--nopause" pause
exit /b 1
