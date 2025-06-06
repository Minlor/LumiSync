import os
import subprocess
import sys
from threading import Thread

import colorama
from colorama import Fore

from . import connection

# Import GUI module
try:
    from .gui import run_gui
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False


def main() -> None:
    """The main function running the program."""
    server, devices = connection.connect()
    try:
        colorama.init(True)
        print(Fore.MAGENTA + f"Welcome to {Fore.LIGHTBLUE_EX}LumiSync!")
        print(Fore.YELLOW + "Please select a option:")
        print(Fore.GREEN + "1) Monitor Sync" "\n2) Music Sync" "\n3) Launch GUI" "\n9) Run test")

        mode = input("")
        match mode:
            case "1" | "2":
                if mode == "1":
                    from .sync import monitor as sync
                else:
                    from .sync import music as sync

                # NOTE: Right now for testing and development this is limited to 1 device.
                thread = Thread(
                    daemon=True,
                    target=sync.start,
                    name="sync",
                    kwargs={"server": server, "device": devices[0]},
                )
                thread.start()

                # TODO: Remove after development is finished
                input("Press Enter to exit...")
            case "3":
                print(Fore.GREEN + "Launching GUI...")
                run_gui()

            case "9":
                # HACK: For now close server to run tests
                server.close()

                files = list(enumerate(os.listdir("tests"), 1))
                print(
                    f"{Fore.LIGHTYELLOW_EX}Chose test to run:\n{Fore.YELLOW}"
                    + "\n".join([f"{i}) {x}" for i, x in files])
                )
                # TODO: Tests sometimes appear in different order -> Keep the same
                # TODO: Implement testing framework like pytest/unittest for the tests
                test = input("Test: ")
                for i, x in files:
                    if i == int(test):
                        subprocess.run(["python", f"tests/{x}"], check=True)
            case _:
                input(Fore.RED + "Invalid option!\nPress Enter to exit...")
                sys.exit()
    finally:
        server.close()
