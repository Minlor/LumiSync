import json
import time

from utils import GetDevices

def get_device_data():
    try:
        with open("Settings.json", "r") as f:
            data = json.load(f)

        if time.time() - data.get("Time", 0) > 86400:
            print("Device data is older than 24 hours, requesting new data...")
            data = GetDevices.start()
        return data
    except FileNotFoundError:
        print("Settings.json not found, requesting new data...")
        data = GetDevices.start()
        return data