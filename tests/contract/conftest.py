"""Ensure the repo shared paths are importable for the contract suites."""
import os
import sys

_HERE = os.path.dirname(__file__)
for path in (_HERE, os.path.join(_HERE, "..", "..", "services", "_shared")):
    path = os.path.abspath(path)
    if path not in sys.path:
        sys.path.insert(0, path)
