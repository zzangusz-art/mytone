@echo off
chcp 65001 >nul
setlocal
rem 내말투 상담 - 체험판 다시 만들어 GitHub 에 올리기 (공개 저장소 mytone-preview 로 푸시됩니다)
cd /d "%~dp0"
"%LOCALAPPDATA%\MyTone\venv\Scripts\python.exe" -X utf8 scripts\build_preview.py
if errorlevel 1 goto :fail
cd preview
if not exist ".git" (
  echo preview 폴더가 깃 저장소가 아닙니다. project.md 의 체험판 항목을 확인해 주세요.
  goto :fail
)
git add -A
git diff --cached --quiet
if not errorlevel 1 (
  echo 바뀐 내용이 없습니다. 올리지 않았습니다.
  goto :done
)
git commit -q -m "체험판 갱신"
if errorlevel 1 goto :fail
git push
if errorlevel 1 goto :fail
echo 올렸습니다. 1~2분 뒤 https://zzangusz-art.github.io/mytone-preview/ 에 반영됩니다.
:done
if not "%1"=="--nopause" pause
exit /b 0
:fail
echo 끝내지 못했습니다. 위 메시지를 확인해 주세요.
if not "%1"=="--nopause" pause
exit /b 1
