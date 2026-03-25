"""wholesale-mcp 진입점 — .mcp.json + PyInstaller 공용"""
import sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    base_dir = Path(sys._MEIPASS)
else:
    base_dir = Path(__file__).resolve().parent

# src/ 경로 추가
src_dir = base_dir / "src"
if src_dir.is_dir():
    sys.path.insert(0, str(src_dir))
else:
    sys.path.insert(0, str(base_dir))

from wholesale_mcp.server import main

if __name__ == "__main__":
    main()
