"""wholesale-mcp 진입점 — .mcp.json + PyInstaller 공용"""
import sys
import os

if getattr(sys, 'frozen', False):
    base_dir = sys._MEIPASS
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

# src/ 경로 추가
src_dir = os.path.join(base_dir, "src")
if os.path.isdir(src_dir):
    sys.path.insert(0, src_dir)
else:
    sys.path.insert(0, base_dir)

from wholesale_mcp.server import main

if __name__ == "__main__":
    main()
