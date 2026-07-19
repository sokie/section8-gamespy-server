#!/usr/bin/env python3
"""Convenience launcher: `python run.py [config.json]` (equivalent to `python -m server`)."""
import sys

from server.__main__ import main

if __name__ == "__main__":
    main(sys.argv)
