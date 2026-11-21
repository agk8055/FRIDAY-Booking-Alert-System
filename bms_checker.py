"""
BookMyShow Checker Module (forwarder to src/checker.py).
"""

import sys
from pathlib import Path

# Add src/ to sys.path for runtime execution
_SRC_DIR = Path(__file__).resolve().parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from src.checker import BMSChecker, CheckResult

__all__ = ["BMSChecker", "CheckResult"]
