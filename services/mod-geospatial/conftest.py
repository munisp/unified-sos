"""Pytest bootstrap for mod-geospatial: make ``app`` importable from the service root."""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
