import os
import sys
from threading import Thread

import colorama

def main():
    colorama.init(True)

    print(colorama.Fore.MAGENTA + f"Welcome to {colorama.Fore.LIGHTBLUE_EX}LumiSync!")
    print(colorama.Fore.YELLOW + "Please select a option:")
    print(colorama.Fore.GREEN + "1) Monitor Sync" "\n2) Music Sync" "\n9) Run test")
    mode = input("")
    match mode:
        case "1" | "2":
            if mode == "1":
                from .modes import MonitorSync as sync
            else:
                from .modes import MusicSync as sync

            thread = Thread(daemon=True, target=sync.start, name="MonitorSync", args=())
            thread.start()
            input("Press Enter to exit...")
        case "9":
            files = list(enumerate(os.listdir("tests"), 1))
            print(
                f"{colorama.Fore.LIGHTYELLOW_EX}Chose test to run:\n{colorama.Fore.YELLOW}"
                + "\n".join([f"{i}) {x}" for i, x in files])
            )
            test = input("Test: ")
            for i, x in files:
                if i == int(test):
                    exec(open(f"tests/{x}").read())
        case _:
            input(colorama.Fore.RED + "Invalid option!\nPress Enter to exit...")
            sys.exit()
