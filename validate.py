"""Validation entrypoint: `python validate.py [--input-dir .] [--out-dir .]` (same as `irp validate`)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from irp.interface.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["validate", *sys.argv[1:]]))
