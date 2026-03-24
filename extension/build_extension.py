#!/usr/bin/env python3
"""
wholesale-mcp Desktop Extension (.mcpb) 빌드 스크립트

Windows에서 실행:
    python extension/build_extension.py

필요:
    - PyInstaller: pip install pyinstaller
    - Node.js: npx @anthropic-ai/mcpb 실행용

생성물:
    - wholesale-mcp.mcpb (Claude Desktop 원클릭 설치 파일)
"""

import os
import json
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
DIST_DIR = PROJECT_DIR / "dist" / "extension"
VERSION = "1.4.0"


def step1_pyinstaller():
    """PyInstaller로 server.py → 단일 .exe 빌드"""
    print("\n[1/4] PyInstaller 빌드 중...")

    subprocess.run([
        sys.executable, "-m", "PyInstaller",
        str(PROJECT_DIR / "wholesale_mcp" / "server.py"),
        "--onefile",
        "--name", "wholesale-mcp",
        "--hidden-import", "wholesale_mcp",
        "--hidden-import", "wholesale_mcp.site_executor",
        "--hidden-import", "wholesale_mcp.browser_engine",
        "--hidden-import", "wholesale_mcp.recipe_normalizer",
        "--hidden-import", "wholesale_mcp.recipe_schema",
        "--distpath", str(PROJECT_DIR / "dist"),
        "--workpath", str(PROJECT_DIR / "build" / "pyinstaller"),
        "--specpath", str(PROJECT_DIR / "build"),
    ], check=True, cwd=str(PROJECT_DIR))

    exe_path = PROJECT_DIR / "dist" / "wholesale-mcp.exe"
    if not exe_path.exists():
        raise FileNotFoundError(f"빌드 실패: {exe_path}")

    print(f"  .exe 생성: {exe_path} ({exe_path.stat().st_size // 1024 // 1024}MB)")


def step2_assemble():
    """dist/extension/ 폴더 구성"""
    print("\n[2/4] Extension 폴더 구성 중...")

    server_dir = DIST_DIR / "server"
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    server_dir.mkdir(parents=True)

    # .exe 복사
    shutil.copy(PROJECT_DIR / "dist" / "wholesale-mcp.exe", server_dir)

    # 레시피 복사
    recipes_src = PROJECT_DIR / "recipes"
    recipes_dst = server_dir / "recipes"
    if recipes_src.exists():
        shutil.copytree(recipes_src, recipes_dst,
                        ignore=shutil.ignore_patterns("*_auto*", "*_test*", "*_backup*"))

    # manifest.json 복사
    shutil.copy(SCRIPT_DIR / "manifest.json", DIST_DIR)

    # 파일 목록 출력
    for f in sorted(DIST_DIR.rglob("*")):
        if f.is_file():
            size = f.stat().st_size
            rel = f.relative_to(DIST_DIR)
            print(f"  {rel} ({size // 1024}KB)")


def step3_mcpb_pack():
    """npx @anthropic-ai/mcpb pack으로 .mcpb 생성"""
    print("\n[3/4] mcpb 패키징 중...")

    output = PROJECT_DIR / f"wholesale-mcp-{VERSION}.mcpb"
    if output.exists():
        output.unlink()

    try:
        subprocess.run([
            "npx", "@anthropic-ai/mcpb", "pack",
            str(DIST_DIR), str(output)
        ], check=True, cwd=str(PROJECT_DIR))
        print(f"  .mcpb 생성: {output} ({output.stat().st_size // 1024 // 1024}MB)")
    except FileNotFoundError:
        print("  [경고] npx를 찾을 수 없습니다. Node.js가 설치되어 있는지 확인하세요.")
        print(f"  수동 패키징: npx @anthropic-ai/mcpb pack {DIST_DIR} {output}")
        # 수동 ZIP 폴백
        print("  ZIP으로 폴백 패키징...")
        shutil.make_archive(str(output).replace('.mcpb', ''), 'zip', str(DIST_DIR))
        zip_path = str(output).replace('.mcpb', '.zip')
        os.rename(zip_path, str(output))
        print(f"  .mcpb (ZIP) 생성: {output}")


def step4_verify():
    """생성된 .mcpb 검증"""
    print("\n[4/4] 검증 중...")

    output = PROJECT_DIR / f"wholesale-mcp-{VERSION}.mcpb"
    if not output.exists():
        print("  [실패] .mcpb 파일이 없습니다.")
        return

    size_mb = output.stat().st_size / 1024 / 1024
    print(f"  파일 크기: {size_mb:.1f}MB")

    if size_mb > 200:
        print("  [경고] 200MB 초과. Chromium이 포함된 것 같습니다.")
    elif size_mb < 1:
        print("  [경고] 1MB 미만. 빌드가 불완전합니다.")
    else:
        print("  [OK] 크기 정상")

    print(f"\n  배포: gh release create v{VERSION} {output.name}")


def main():
    print("=" * 50)
    print(f" wholesale-mcp Desktop Extension 빌드 (v{VERSION})")
    print("=" * 50)

    step1_pyinstaller()
    step2_assemble()
    step3_mcpb_pack()
    step4_verify()

    print("\n" + "=" * 50)
    print(" 빌드 완료!")
    print("=" * 50)


if __name__ == "__main__":
    main()
