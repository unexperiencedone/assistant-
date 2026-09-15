"""Entry point for the local index CLI that works from any working directory:

    python C:\\Assisstant\\sysindex.py find "report" --kind file
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assistant.system.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
