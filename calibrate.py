#!/usr/bin/env python3
"""Measure the Bluetooth delay in a fresh session and save it. Thin wrapper:
the real measurement lives in split.py (it must run inside the same process
that owns the Bluetooth stream, because the latency changes per stream)."""
import os, sys, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.exit(subprocess.call([os.path.join(HERE, "venv/bin/python"), os.path.join(HERE, "split.py"), "--calibrate-only", *sys.argv[1:]]))
