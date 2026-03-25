"""wholesale-mcp 진입점 — .mcp.json + PyInstaller 공용"""
import sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    # PyInstaller: frozen importer가 wholesale_mcp 패키지 처리
    pass
else:
    # 개발 모드: src/ 디렉토리를 sys.path에 추가
    src_dir = Path(__file__).resolve().parent / "src"
    if src_dir.is_dir():
        sys.path.insert(0, str(src_dir))

from wholesale_mcp.server import main

if __name__ == "__main__":
    main()
