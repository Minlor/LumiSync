from pathlib import Path
import sys

# Ensure project root is on sys.path so we can import the package directly
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main():
    # Try to import the packaged GUI entry (`lumisync.gui.run_gui`),
    # fall back to the main_window entry if needed. Provide a clear
    # message if `pythoncom` (pywin32) is missing.
    try:
        from lumisync.gui import run_gui
    except ModuleNotFoundError as e:
        # Detect missing pythoncom dependency and advise user
        msg = str(e)
        if "pythoncom" in msg:
            print("Missing dependency: pythoncom (pywin32). Install with:")
            print("  python -m pip install pywin32")
            raise SystemExit(1)
        # Try direct import as last resort
        try:
            from lumisync.gui.main_window import main as run_gui
        except Exception as exc:
            print("Failed to import GUI module:", exc)
            raise

    run_gui()


if __name__ == "__main__":
    main()
