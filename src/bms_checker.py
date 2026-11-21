"""
Alias for src/checker.py to support both bms_checker and checker imports.
"""
from .checker import BMSChecker, CheckResult

__all__ = ["BMSChecker", "CheckResult"]
