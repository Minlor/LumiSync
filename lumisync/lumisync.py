"""LumiSync entry point.

Default behavior (no args): launch the GUI.
Use `--cli` for the legacy interactive terminal menu.
Direct subcommands (`--monitor`, `--music`, `--test`) bypass the menu.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from threading import Thread

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "lumisync"

import colorama
from colorama import Fore

from . import connection
from .utils.logging import setup_logger

logger = setup_logger("lumisync")


def _ensure_standard_streams() -> None:
    """Keep argparse usable in windowed frozen builds."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _launch_gui() -> int:
    """Import and run the GUI. Imported lazily so CLI mode skips GUI deps."""
    try:
        from .gui import run_gui
    except ImportError as e:
        logger.error("Failed to load GUI module", exc_info=True)
        print(f"{Fore.RED}GUI module failed to load: {e}")
        print("Run with --cli to use the terminal menu instead.")
        return 1
    run_gui()
    return 0


def _connect_for_cli():
    """Connect and return (server, devices) for CLI sync paths."""
    server, devices = connection.connect()
    logger.info(f"Found {len(devices)} device(s)")
    return server, devices


def _start_sync(mode: str) -> int:
    """Start monitor or music sync in a thread; block on Enter."""
    server = None
    try:
        server, devices = _connect_for_cli()
        if not devices:
            print(f"{Fore.RED}No devices found.")
            return 1

        if mode == "monitor":
            from .sync import monitor as sync
        else:
            from .sync import music as sync

        thread = Thread(
            daemon=True,
            target=sync.start,
            name="sync",
            kwargs={"server": server, "device": devices[0]},
        )
        thread.start()
        logger.info(f"Started {mode} sync")
        input(f"{Fore.GREEN}{mode.capitalize()} sync running. Press Enter to exit...")
        return 0
    except Exception as e:
        logger.critical(f"Sync error: {e}", exc_info=True)
        return 1
    finally:
        if server is not None:
            try:
                server.close()
            except Exception:
                pass


def _run_tests() -> int:
    """List tests and run the chosen one."""
    files = list(enumerate(os.listdir("tests"), 1))
    print(
        f"{Fore.LIGHTYELLOW_EX}Choose test to run:\n{Fore.YELLOW}"
        + "\n".join([f"{i}) {x}" for i, x in files])
    )
    test = input("Test: ")
    try:
        choice = int(test)
    except ValueError:
        print(f"{Fore.RED}Invalid selection")
        return 1
    for i, x in files:
        if i == choice:
            try:
                subprocess.run(["python", f"tests/{x}"], check=True)
                return 0
            except subprocess.CalledProcessError as e:
                logger.error(f"Test {x} failed (exit {e.returncode})", exc_info=True)
                return e.returncode
    print(f"{Fore.RED}No test with that number")
    return 1


def _run_cli_menu() -> int:
    """The legacy interactive terminal menu (--cli)."""
    server = None
    try:
        server, devices = _connect_for_cli()

        colorama.init(True)
        print(Fore.MAGENTA + f"Welcome to {Fore.LIGHTBLUE_EX}LumiSync!")
        print(Fore.YELLOW + "Please select a option:")
        print(Fore.GREEN + "1) Monitor Sync\n2) Music Sync\n3) Launch GUI\n9) Run test")

        mode = input("")
        logger.info(f"User selected mode: {mode}")
        match mode:
            case "1" | "2":
                if not devices:
                    print(f"{Fore.RED}No devices found.")
                    return 1
                if mode == "1":
                    from .sync import monitor as sync
                else:
                    from .sync import music as sync
                thread = Thread(
                    daemon=True,
                    target=sync.start,
                    name="sync",
                    kwargs={"server": server, "device": devices[0]},
                )
                thread.start()
                input("Press Enter to exit...")
                return 0
            case "3":
                # Close discovery server before handing off to the GUI
                if server is not None:
                    server.close()
                    server = None
                return _launch_gui()
            case "9":
                if server is not None:
                    server.close()
                    server = None
                return _run_tests()
            case _:
                input(Fore.RED + "Invalid option!\nPress Enter to exit...")
                return 1
    except Exception as e:
        logger.critical(f"Critical error in CLI: {e}", exc_info=True)
        return 1
    finally:
        if server is not None:
            try:
                server.close()
            except Exception:
                pass


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lumisync",
        description="Sync Govee lights with your screen and audio. Default: launch GUI.",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--cli", "-c", action="store_true", help="Open the interactive terminal menu instead of the GUI.")
    g.add_argument("--monitor", action="store_true", help="Start monitor sync directly (headless).")
    g.add_argument("--music", action="store_true", help="Start music sync directly (headless).")
    g.add_argument("--test", action="store_true", help="Run the test selector.")
    return p


def main() -> None:
    _ensure_standard_streams()

    parser = _build_parser()
    args = parser.parse_args()

    if args.cli:
        sys.exit(_run_cli_menu())
    if args.monitor:
        sys.exit(_start_sync("monitor"))
    if args.music:
        sys.exit(_start_sync("music"))
    if args.test:
        sys.exit(_run_tests())

    # Default: GUI. No discovery here — the GUI handles its own.
    sys.exit(_launch_gui())


if __name__ == "__main__":
    main()
