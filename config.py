"""
Configuration Module (forwarder to src/config.py).
"""

import sys
from pathlib import Path

# Add src/ to sys.path for runtime execution
_SRC_DIR = Path(__file__).resolve().parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from src.config import (
    BOOKINGS_LOG_FILE,
    LOGS_DIR,
    MONITOR_LOG_FILE,
    PROJECT_ROOT,
    Config,
    TargetItem,
    TheatreTarget,
    config,
    load_targets_from_file_or_env,
    parse_theatres,
)

__all__ = [
    "Config",
    "TargetItem",
    "TheatreTarget",
    "config",
    "load_targets_from_file_or_env",
    "parse_theatres",
    "BOOKINGS_LOG_FILE",
    "MONITOR_LOG_FILE",
    "LOGS_DIR",
    "PROJECT_ROOT",
]
