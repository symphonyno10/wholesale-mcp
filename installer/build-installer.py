#!/usr/bin/env python3
"""
wholesale-mcp Windows 인스톨러 빌드 스크립트

사용법 (Windows에서):
    python build-installer.py

필요:
    - Inno Setup 6 설치 (https://jrsoftware.org/isinfo.php)
    - 인터넷 연결 (Python embedded 다운로드)

생성물:
    - installer/output/wholesale-mcp-setup.exe
"""

import os
import sys
import json
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

PYTHON_VERSION = "3.13.5"
PYTHON_EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

SCRIPT_DIR = Path(__file__).parent
DIST_DIR = SCRIPT_DIR / "dist"
PYTHON_DIR = DIST_DIR / "python"
OUTPUT_DIR = SCRIPT_DIR / "output"


def download(url: str, dest: Path):
    """URL 다운로드"""
    print(f"  다운로드: {url.split('/')[-1]}")
    urllib.request.urlretrieve(url, dest)


def step1_python_embedded():
    """Python embedded 다운로드 + 압축 해제"""
    print("\n[1/5] Python embedded 준비")

    if PYTHON_DIR.exists():
        shutil.rmtree(PYTHON_DIR)
    PYTHON_DIR.mkdir(parents=True)

    zip_path = SCRIPT_DIR / "python-embed.zip"
    download(PYTHON_EMBED_URL, zip_path)

    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(PYTHON_DIR)
    zip_path.unlink()

    # pip 활성화: python3XX._pth에서 import site 주석 해제
    pth_files = list(PYTHON_DIR.glob("python*._pth"))
    for pth in pth_files:
        content = pth.read_text()
        content = content.replace("#import site", "import site")
        pth.write_text(content)

    print(f"  Python {PYTHON_VERSION} embedded → {PYTHON_DIR}")


def step2_install_pip():
    """get-pip.py로 pip 설치"""
    print("\n[2/5] pip 설치")

    get_pip = SCRIPT_DIR / "get-pip.py"
    download(GET_PIP_URL, get_pip)

    python_exe = PYTHON_DIR / "python.exe"
    subprocess.run([str(python_exe), str(get_pip), "--no-warn-script-location"],
                   check=True, cwd=str(PYTHON_DIR))
    get_pip.unlink()
    print("  pip 설치 완료")


def step3_install_packages():
    """wholesale-mcp + 의존성 설치"""
    print("\n[3/5] wholesale-mcp 패키지 설치")

    python_exe = PYTHON_DIR / "python.exe"

    # wholesale-mcp 설치
    subprocess.run([
        str(python_exe), "-m", "pip", "install",
        "wholesale-mcp", "--no-warn-script-location"
    ], check=True)

    print("  wholesale-mcp + 의존성 설치 완료")


def step4_copy_files():
    """server.py shim + post-install.py 복사"""
    print("\n[4/5] 추가 파일 복사")

    # server.py shim
    server_shim = DIST_DIR / "server.py"
    server_shim.write_text(
        '"""Backward compatibility shim."""\n'
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(__file__))\n'
        'from wholesale_mcp.server import main\n'
        'if __name__ == "__main__":\n'
        '    main()\n'
    )

    # post-install.py
    shutil.copy(SCRIPT_DIR / "post-install.py", DIST_DIR / "post-install.py")

    # credentials.example.json
    example_cred = DIST_DIR / "credentials.example.json"
    example_cred.write_text(json.dumps({
        "example_site_com": {
            "username": "your_id",
            "password": "your_password"
        }
    }, indent=2, ensure_ascii=False))

    print("  server.py, post-install.py, credentials.example.json 복사 완료")


def step5_build_installer():
    """Inno Setup으로 인스톨러 빌드"""
    print("\n[5/5] Inno Setup 빌드")

    iss_file = SCRIPT_DIR / "wholesale-mcp-setup.iss"

    # Inno Setup 컴파일러 찾기
    iscc_paths = [
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]

    iscc = None
    for p in iscc_paths:
        if os.path.exists(p):
            iscc = p
            break

    if not iscc:
        print("  [경고] Inno Setup이 설치되어 있지 않습니다.")
        print("  https://jrsoftware.org/isinfo.php 에서 설치 후 다시 실행하세요.")
        print(f"  또는 직접 컴파일: ISCC.exe {iss_file}")
        return

    OUTPUT_DIR.mkdir(exist_ok=True)
    subprocess.run([iscc, str(iss_file)], check=True)
    print(f"\n  인스톨러 생성: {OUTPUT_DIR / 'wholesale-mcp-setup.exe'}")


def main():
    print("=" * 50)
    print(" wholesale-mcp 인스톨러 빌드")
    print("=" * 50)

    # 클린 빌드
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True)

    step1_python_embedded()
    step2_install_pip()
    step3_install_packages()
    step4_copy_files()
    step5_build_installer()

    print("\n" + "=" * 50)
    print(" 빌드 완료!")
    print("=" * 50)


if __name__ == "__main__":
    main()
