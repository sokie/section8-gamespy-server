#!/usr/bin/env python3
"""Convenience launcher: `python run.py [config.json]` (equivalent to `python -m server`)."""

import os
import sys

from server.__main__ import main

if __name__ == "__main__":
    # A frozen build resolves config.json, the database and the log next to the executable rather
    # than from whatever directory it happened to be started in.
    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(sys.executable))
    main(sys.argv)
