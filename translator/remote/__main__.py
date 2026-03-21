"""Entry point: python -m translator.remote"""
import sys
from pathlib import Path

# Ensure project root is on path when run as a module
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server import main  # noqa: E402

main()
