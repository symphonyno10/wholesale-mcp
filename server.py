"""Backward compatibility shim — delegates to wholesale_mcp.server."""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from wholesale_mcp.server import main
if __name__ == "__main__":
    main()
