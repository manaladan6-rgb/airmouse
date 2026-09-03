"""Pytest bootstrap for AirMouse v9 test suite.

Adds the package source tree (airmouse_pkg/) to sys.path so tests run
directly from a source checkout without installing the wheel.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = os.path.join(_ROOT, "airmouse_pkg")
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)
