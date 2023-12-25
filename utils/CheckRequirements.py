import importlib
import os


def check_requirements():
    try:
        for x in open("requirements.txt").read().split("\n"):
            if x == "Pillow":
                x = "PIL"
            importlib.import_module(x)
    except ModuleNotFoundError:
        print("Error, some requirements are missing!")
        install = input("Would you like to install them? (y/n): ")
        if install.lower() == "y":
            os.system("pip install -r requirements.txt")
            os.system("cls" if os.name == "nt" else "clear")
            print("Requirements installed!")
        else:
            print("Exiting...")
            exit(1)