from utils import CheckRequirements
CheckRequirements.check_requirements()

import os
import colorama
from threading import Thread


colorama.init(True)

print(colorama.Fore.MAGENTA + f"Welcome to {colorama.Fore.LIGHTBLUE_EX}LumiSync!")
print(colorama.Fore.YELLOW + "Please select a option:")
print(colorama.Fore.GREEN + "1) Monitor Sync")
print(colorama.Fore.GREEN + "9) Tests")
mode = input("")
match mode:
    case "1":
        from modes import MonitorSync
        thread = Thread(daemon=True, target=MonitorSync.start, name="MonitorSync", args=())
        thread.start()
        input("Press Enter to exit...")
    case "9":
        print(f"Chose test to run:\n" + "\n".join([f"{i+1}) {x}" for i, x in enumerate(os.listdir("tests"))]))
        test = input("Test: ")
        files = enumerate(os.listdir("tests"), 1)
        for i, x in files:
            if i == int(test):
                exec(open(f"tests/{x}").read())



    case _:
        input(colorama.Fore.RED + "Invalid option!\nPress Enter to exit...")
        exit(1)

if __name__ == "__main__":
    pass