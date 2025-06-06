import os
import subprocess
import sys
from threading import Thread

import colorama
from colorama import Fore

from . import connection
from .utils.logging import setup_logger

# Set up logger for main application
logger = setup_logger('lumisync')

# Import GUI module
try:
    from .gui import run_gui
    GUI_AVAILABLE = True
    logger.info("GUI module loaded successfully")
except ImportError:
    GUI_AVAILABLE = False
    logger.error("Failed to load GUI module", exc_info=True)


def main() -> None:
    """The main function running the program."""
    logger.info("Starting LumiSync application")
    try:
        server, devices = connection.connect()
        logger.info(f"Found {len(devices)} device(s)")
        
        colorama.init(True)
        print(Fore.MAGENTA + f"Welcome to {Fore.LIGHTBLUE_EX}LumiSync!")
        print(Fore.YELLOW + "Please select a option:")
        print(Fore.GREEN + "1) Monitor Sync" "\n2) Music Sync" "\n3) Launch GUI" "\n9) Run test")

        mode = input("")
        logger.info(f"User selected mode: {mode}")
        match mode:
            case "1" | "2":
                if mode == "1":
                    from .sync import monitor as sync
                    logger.info("Selected Monitor Sync mode")
                else:
                    from .sync import music as sync
                    logger.info("Selected Music Sync mode")

                # NOTE: Right now for testing and development this is limited to 1 device.
                thread = Thread(
                    daemon=True,
                    target=sync.start,
                    name="sync",
                    kwargs={"server": server, "device": devices[0]},
                )
                thread.start()
                logger.info(f"Started {mode} sync thread")

                # TODO: Remove after development is finished
                input("Press Enter to exit...")
                logger.info("User terminated sync")
            case "3":
                print(Fore.GREEN + "Launching GUI...")
                logger.info("Launching GUI application")
                run_gui()

            case "9":
                # HACK: For now close server to run tests
                server.close()
                logger.info("Closed server for tests")

                files = list(enumerate(os.listdir("tests"), 1))
                print(
                    f"{Fore.LIGHTYELLOW_EX}Chose test to run:\n{Fore.YELLOW}"
                    + "\n".join([f"{i}) {x}" for i, x in files])
                )
                # TODO: Tests sometimes appear in different order -> Keep the same
                # TODO: Implement testing framework like pytest/unittest for the tests
                test = input("Test: ")
                logger.info(f"Selected test: {test}")
                for i, x in files:
                    if i == int(test):
                        logger.info(f"Running test: {x}")
                        try:
                            subprocess.run(["python", f"tests/{x}"], check=True)
                            logger.info(f"Test {x} completed successfully")
                        except subprocess.CalledProcessError as e:
                            logger.error(f"Test {x} failed with exit code {e.returncode}", exc_info=True)
            case _:
                logger.warning(f"Invalid option entered: {mode}")
                input(Fore.RED + "Invalid option!\nPress Enter to exit...")
                sys.exit()
    except Exception as e:
        logger.critical(f"Critical error in main function: {str(e)}", exc_info=True)
    finally:
        if 'server' in locals():
            server.close()
            logger.info("Server closed")
        logger.info("Application terminated")
