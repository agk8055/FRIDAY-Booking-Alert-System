#!/usr/bin/env python3
"""
FRIDAY - BookMyShow Booking Alert & Monitoring System
CLI entrypoint (wrapper for src/monitor.py).
"""

import os
import sys

# Add src directory to module search path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from monitor import main

if __name__ == "__main__":
    main()
