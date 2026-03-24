"""PyInstaller entry point — avoids relative import issues."""
import sys
import os

# Ensure wholesale_mcp package is importable
if getattr(sys, 'frozen', False):
    # Running as PyInstaller bundle
    base_dir = sys._MEIPASS
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, base_dir)

from wholesale_mcp.server import main

if __name__ == "__main__":
    main()
