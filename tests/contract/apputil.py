"""Shared helpers for contract/security/SAT suites: in-process app loading."""
import importlib
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVICES_DIR = os.path.join(REPO_ROOT, "services")

for rel in ("services/_shared", "contracts/asyncapi/registry", "contracts/openapi"):
    path = os.path.join(REPO_ROOT, rel)
    if path not in sys.path:
        sys.path.insert(0, path)


def load_service_app(service: str, package: str = "app.main"):
    """Import a service's FastAPI app factory in-process from its own dir.

    Mirrors contracts/openapi/generate_from_apps.py: the service directory is
    put on sys.path and any previously imported same-named package is evicted,
    so suites can load several ``app.main`` packages in one pytest process.
    """
    svc_dir = os.path.join(SERVICES_DIR, service)
    sys.path.insert(0, svc_dir)
    pkg_root = package.split(".")[0]
    for mod in [m for m in list(sys.modules) if m == pkg_root or m.startswith(pkg_root + ".")]:
        del sys.modules[mod]
    try:
        module = importlib.import_module(package)
        return module
    finally:
        sys.path.remove(svc_dir)
