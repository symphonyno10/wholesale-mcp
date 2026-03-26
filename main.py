"""wholesale-mcp 진입점 — .mcp.json + PyInstaller 공용"""
import os
import sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    # PyInstaller: 호스트 시스템 Python 격리
    os.environ.pop("PYTHONPATH", None)
    os.environ.pop("PYTHONHOME", None)

    # 번들된 Playwright 브라우저 경로 설정
    _meipass = Path(sys._MEIPASS)
    _browsers = _meipass / "playwright-browsers"
    if _browsers.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_browsers)
else:
    # 개발 모드: src/ 디렉토리를 sys.path에 추가
    src_dir = Path(__file__).resolve().parent / "src"
    if src_dir.is_dir():
        sys.path.insert(0, str(src_dir))

from wholesale_mcp.server import main

if __name__ == "__main__":
    main()
