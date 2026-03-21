# wholesale-mcp Windows 설치 스크립트
# PowerShell에서 실행: irm https://raw.githubusercontent.com/symphonyno10/wholesale-mcp/main/install.ps1 | iex

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== wholesale-mcp 설치 ===" -ForegroundColor Cyan
Write-Host ""

# 설치 경로
$InstallDir = "$env:USERPROFILE\wholesale-mcp"

# Python 확인
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "[ERROR] Python이 설치되어 있지 않습니다." -ForegroundColor Red
    Write-Host "https://www.python.org/downloads/ 에서 설치 후 다시 실행하세요."
    exit 1
}
Write-Host "[OK] Python: $(python --version)" -ForegroundColor Green

# Git 확인
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Write-Host "[ERROR] Git이 설치되어 있지 않습니다." -ForegroundColor Red
    Write-Host "https://git-scm.com/downloads 에서 설치 후 다시 실행하세요."
    exit 1
}
Write-Host "[OK] Git: $(git --version)" -ForegroundColor Green

# 이미 설치되어 있으면 업데이트
if (Test-Path $InstallDir) {
    Write-Host ""
    Write-Host "기존 설치 발견. 업데이트합니다..." -ForegroundColor Yellow
    Set-Location $InstallDir
    git pull origin main
} else {
    Write-Host ""
    Write-Host "다운로드 중..." -ForegroundColor Yellow
    git clone https://github.com/symphonyno10/wholesale-mcp.git $InstallDir
    Set-Location $InstallDir
}

# 가상환경 생성
if (-not (Test-Path "venv")) {
    Write-Host "가상환경 생성 중..." -ForegroundColor Yellow
    python -m venv venv
}

# 의존성 설치
Write-Host "의존성 설치 중..." -ForegroundColor Yellow
& "venv\Scripts\pip.exe" install -r requirements.txt --quiet

# Playwright 설치
Write-Host "Playwright 브라우저 설치 중..." -ForegroundColor Yellow
& "venv\Scripts\playwright.exe" install chromium

# credentials.json 생성
if (-not (Test-Path "credentials.json")) {
    Copy-Item "credentials.example.json" "credentials.json"
    Write-Host "[OK] credentials.json 생성됨 (ID/PW를 편집하세요)" -ForegroundColor Green
}

# .mcp.json 경로 생성
$PythonPath = "$InstallDir/venv/Scripts/python.exe" -replace '\\', '/'
$ServerPath = "$InstallDir/server.py" -replace '\\', '/'
$CwdPath = "$InstallDir" -replace '\\', '/'

$McpJson = @"
{
  "mcpServers": {
    "wholesale-tools": {
      "command": "$PythonPath",
      "args": ["$ServerPath"],
      "cwd": "$CwdPath"
    }
  }
}
"@

# 완료
Write-Host ""
Write-Host "=== 설치 완료! ===" -ForegroundColor Green
Write-Host ""
Write-Host "설치 경로: $InstallDir" -ForegroundColor White
Write-Host ""
Write-Host "[다음 단계]" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. credentials.json에 사이트별 ID/PW를 입력하세요:" -ForegroundColor White
Write-Host "   notepad $InstallDir\credentials.json" -ForegroundColor Gray
Write-Host ""
Write-Host "2. AI 도구(Claude Code, Cursor 등)의 .mcp.json에 아래 내용을 복사하세요:" -ForegroundColor White
Write-Host ""
Write-Host $McpJson -ForegroundColor Yellow
Write-Host ""
Write-Host "3. AI 도구를 재시작하면 사용 가능합니다." -ForegroundColor White
Write-Host ""

# .mcp.json 자동 저장 여부
$save = Read-Host ".mcp.json을 이 프로젝트 폴더에 자동 생성할까요? (y/n)"
if ($save -eq "y") {
    $McpJson | Out-File -FilePath "$InstallDir\.mcp.json" -Encoding utf8
    Write-Host "[OK] .mcp.json 생성 완료" -ForegroundColor Green
}
