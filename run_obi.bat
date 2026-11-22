@echo off
chcp 65001 >nul
setlocal
rem OBI (Offboarding Inspector) 실행 - 이 파일을 더블클릭하세요.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [오류] Python 을 찾을 수 없습니다. Python 3.12 이상을 설치하고 PATH 에 추가하세요.
    pause
    exit /b 1
)

python -c "import streamlit, pandas, openpyxl, xlrd" >nul 2>nul
if errorlevel 1 (
    echo 필요한 패키지를 설치합니다...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [오류] 패키지 설치에 실패했습니다.
        pause
        exit /b 1
    )
)

echo OBI 를 시작합니다. 브라우저가 자동으로 열립니다. 종료하려면 이 창을 닫거나 Ctrl+C 를 누르세요.
python -m streamlit run app.py
pause
