from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if SRC.exists():
    sys.path.insert(0, str(SRC))

if sys.platform == "win32":
    try:
        from dev_toolbox.display import enable_windows_dpi_awareness

        enable_windows_dpi_awareness()
    except Exception:
        pass

from dev_toolbox.app import main


if __name__ == "__main__":
    main()
