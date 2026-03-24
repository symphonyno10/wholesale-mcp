@echo off
chcp 65001 >nul
echo.
echo ===================================================
echo  wholesale-mcp 설치
echo  도매 사이트 자동 주문 MCP 서버
echo ===================================================
echo.

:: Python 확인
python --version >nul 2>&1
if errorlevel 1 (
    py --version >nul 2>&1
    if errorlevel 1 (
        echo [!] Python이 설치되어 있지 않습니다.
        echo     자동으로 설치합니다...
        echo.
        winget install Python.Python.3.13 --accept-package-agreements --accept-source-agreements
        if errorlevel 1 (
            echo.
            echo [오류] Python 자동 설치에 실패했습니다.
            echo        https://www.python.org/downloads/ 에서 직접 설치해주세요.
            echo        설치 시 "Add Python to PATH" 체크를 반드시 하세요!
            echo.
            pause
            exit /b 1
        )
        echo.
        echo [OK] Python 설치 완료. 나머지를 자동 설치합니다...
        echo.
        :: 새 cmd에서 자기 자신을 재실행 (PATH 갱신)
        start cmd /k "%~f0"
        exit
    )
    set PYTHON=py
) else (
    set PYTHON=python
)

echo [1/3] wholesale-mcp 패키지 설치 중...
%PYTHON% -m pip install --upgrade wholesale-mcp --quiet
if errorlevel 1 (
    echo [오류] 패키지 설치 실패
    pause
    exit /b 1
)
echo       완료
echo.

echo [2/3] 브라우저(Chromium) 설치 중... (약 1~2분)
%PYTHON% -m playwright install chromium
if errorlevel 1 (
    echo [오류] 브라우저 설치 실패
    pause
    exit /b 1
)
echo       완료
echo.

echo [3/3] Claude Desktop 연동 설정 중...
%PYTHON% -c "import json,os;p=os.path.join(os.environ.get('APPDATA',''),'Claude','claude_desktop_config.json');c={};exec(\"try:\n with open(p) as f: c=json.load(f)\nexcept: pass\");c.setdefault('mcpServers',{})['wholesale-tools']={'command':'python','args':['-m','wholesale_mcp.server']};os.makedirs(os.path.dirname(p),exist_ok=True);open(p,'w').write(json.dumps(c,indent=2));print('      완료: '+p)"
echo.

echo ===================================================
echo  설치 완료!
echo ===================================================
echo.
echo  Claude Desktop을 재시작하면 사용 가능합니다.
echo.
echo  사용법:
echo    "https://도매사이트.com id:아이디 pass:비밀번호 등록해줘"
echo    "타이레놀 검색해줘"
echo    "650mg 5개 담아줘"
echo    "이번 달 매출원장 보여줘"
echo.
pause
