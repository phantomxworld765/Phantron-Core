# -*- coding: utf-8 -*-
"""
PHANTRON v2  -  P7's personal Jarvis-style AI assistant.

A clean, dependency-light rebuild. The CORE runs on the Python standard
library alone (no pip installs required to boot). Optional features such as
voice, screen capture and mouse/keyboard automation light up automatically
when their libraries are available, and are skipped gracefully when they are
not - PHANTRON never crashes just because a library is missing.

Run it with:   python -m phantron
"""

__version__ = "2.0.0"
__all__ = ["__version__"]
