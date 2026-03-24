"""
설치 후 자동 실행 스크립트
- Playwright Chromium 다운로드
- Claude Desktop config 자동 등록
"""

import json
import os
import subprocess
import sys


def install_chromium():
    """Playwright Chromium 브라우저 설치"""
    app_dir = os.path.dirname(os.path.abspath(__file__))
    python = os.path.join(app_dir, "python", "python.exe")
    browsers_dir = os.path.join(app_dir, "browsers")

    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = browsers_dir

    print("브라우저(Chromium) 설치 중... (최초 1회, 약 1~2분 소요)")
    result = subprocess.run(
        [python, "-m", "playwright", "install", "chromium"],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("브라우저 설치 완료")
    else:
        print(f"브라우저 설치 실패: {result.stderr[:200]}")
        print("수동 설치: python -m playwright install chromium")


def register_claude_desktop():
    """Claude Desktop MCP 설정 자동 등록"""
    app_dir = os.path.dirname(os.path.abspath(__file__))
    python_path = os.path.join(app_dir, "python", "python.exe").replace("\\", "/")
    server_path = os.path.join(app_dir, "server.py").replace("\\", "/")
    cwd_path = app_dir.replace("\\", "/")
    browsers_path = os.path.join(app_dir, "browsers").replace("\\", "/")

    # Claude Desktop config 경로
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        print("APPDATA 경로를 찾을 수 없습니다. Claude Desktop 설정을 수동으로 해주세요.")
        return

    config_path = os.path.join(appdata, "Claude", "claude_desktop_config.json")

    # 기존 config 읽기 (있으면)
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            config = {}

    # wholesale-tools MCP 서버 등록
    config.setdefault("mcpServers", {})["wholesale-tools"] = {
        "command": python_path,
        "args": [server_path],
        "cwd": cwd_path,
        "env": {
            "PLAYWRIGHT_BROWSERS_PATH": browsers_path,
        },
    }

    # 저장
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f"Claude Desktop 설정 완료: {config_path}")


def create_credentials():
    """빈 credentials.json 생성 (없으면)"""
    app_dir = os.path.dirname(os.path.abspath(__file__))
    cred_path = os.path.join(app_dir, "credentials.json")

    if not os.path.exists(cred_path):
        with open(cred_path, "w", encoding="utf-8") as f:
            json.dump({}, f)
        print(f"credentials.json 생성: {cred_path}")


def main():
    print("")
    print("=" * 50)
    print(" wholesale-mcp 설치 마무리")
    print("=" * 50)
    print("")

    install_chromium()
    print("")
    register_claude_desktop()
    print("")
    create_credentials()

    print("")
    print("=" * 50)
    print(" 설치 완료!")
    print("=" * 50)
    print("")
    print("Claude Desktop을 재시작하면 사용 가능합니다.")
    print("")
    print('사용법: "https://도매사이트.com id:아이디 pass:비밀번호 등록해줘"')
    print("")


if __name__ == "__main__":
    main()
