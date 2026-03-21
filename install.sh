#!/bin/bash
# wholesale-mcp macOS/Linux 설치 스크립트
# 실행: curl -fsSL https://raw.githubusercontent.com/symphonyno10/wholesale-mcp/main/install.sh | bash

set -e

echo ""
echo "=== wholesale-mcp 설치 ==="
echo ""

INSTALL_DIR="$HOME/wholesale-mcp"

# Python 확인
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python3이 설치되어 있지 않습니다."
    echo "brew install python3 또는 https://www.python.org/downloads/"
    exit 1
fi
echo "[OK] Python: $(python3 --version)"

# Git 확인
if ! command -v git &> /dev/null; then
    echo "[ERROR] Git이 설치되어 있지 않습니다."
    exit 1
fi
echo "[OK] Git: $(git --version)"

# 이미 설치되어 있으면 업데이트
if [ -d "$INSTALL_DIR" ]; then
    echo ""
    echo "기존 설치 발견. 업데이트합니다..."
    cd "$INSTALL_DIR"
    git pull origin main
else
    echo ""
    echo "다운로드 중..."
    git clone https://github.com/symphonyno10/wholesale-mcp.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# 가상환경 생성
if [ ! -d "venv" ]; then
    echo "가상환경 생성 중..."
    python3 -m venv venv
fi

# 의존성 설치
echo "의존성 설치 중..."
venv/bin/pip install -r requirements.txt --quiet

# Playwright 설치
echo "Playwright 브라우저 설치 중..."
venv/bin/playwright install chromium

# credentials.json 생성
if [ ! -f "credentials.json" ]; then
    cp credentials.example.json credentials.json
    echo "[OK] credentials.json 생성됨 (ID/PW를 편집하세요)"
fi

# .mcp.json 경로
PYTHON_PATH="$INSTALL_DIR/venv/bin/python"
SERVER_PATH="$INSTALL_DIR/server.py"

MCP_JSON=$(cat <<EOF
{
  "mcpServers": {
    "wholesale-tools": {
      "command": "$PYTHON_PATH",
      "args": ["$SERVER_PATH"],
      "cwd": "$INSTALL_DIR"
    }
  }
}
EOF
)

# 완료
echo ""
echo "=== 설치 완료! ==="
echo ""
echo "설치 경로: $INSTALL_DIR"
echo ""
echo "[다음 단계]"
echo ""
echo "1. credentials.json에 사이트별 ID/PW를 입력하세요:"
echo "   nano $INSTALL_DIR/credentials.json"
echo ""
echo "2. AI 도구의 .mcp.json에 아래 내용을 복사하세요:"
echo ""
echo "$MCP_JSON"
echo ""
echo "3. AI 도구를 재시작하면 사용 가능합니다."
echo ""

# .mcp.json 자동 저장
read -p ".mcp.json을 이 프로젝트 폴더에 자동 생성할까요? (y/n) " save
if [ "$save" = "y" ]; then
    echo "$MCP_JSON" > "$INSTALL_DIR/.mcp.json"
    echo "[OK] .mcp.json 생성 완료"
fi
