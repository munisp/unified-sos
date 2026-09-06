import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for rel in ("services/_shared", "contracts/asyncapi/registry"):
    path = os.path.join(REPO_ROOT, rel)
    if path not in sys.path:
        sys.path.insert(0, path)
