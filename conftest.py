import sys
from pathlib import Path

# Ensure the project root is on sys.path so test modules can
# import top-level packages (analysis, ui, data, etc.) when
# pytest is invoked as plain `pytest`.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
