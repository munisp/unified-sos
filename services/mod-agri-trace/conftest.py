import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here))          # the service package (``app``)
sys.path.insert(0, str(_here.parent))   # services/ root (``_shared``)
