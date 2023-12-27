import os
import pkgutil


def check_requirements():
        for x in open("requirements.txt").read().split("\n"):
            if x == "Pillow":
                x = "PIL"
            if not pkgutil.find_loader(x):
                print(f"Error, {x} is missing!")
                install = input("Would you like to install it? (y/n): ")
                if install.lower() == "y":
                    os.system(f"pip install {x}")
                    os.system("cls" if os.name == "nt" else "clear")
                    print("Requirement installed!")
                else:
                    print("Exiting...")
                    exit(1)