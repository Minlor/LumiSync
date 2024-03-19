import json
import time
import colorama

from utils import GetDevices

def get_device_data():
    try:
        with open("Settings.json", "r") as f:
            data = json.load(f)

        if time.time() - data.get("time", 0) > 86400:
            print("Device data is older than 24 hours, requesting new data...")
            data = GetDevices.start()
        if len(data["devices"]) > 1:
            print(colorama.Fore.YELLOW + "Please select a device:")
            for device in data["devices"]:
                print(colorama.Fore.LIGHTGREEN_EX + f"{data['devices'].index(device) + 1}) {device['Device_IP']} ({device['Model']})")
            selectedDevice = input("")
            data["selectedDevice"] = int(selectedDevice) - 1
            GetDevices.writeJSON(data)
        return data
    except FileNotFoundError:
        print("Settings.json not found, requesting new data...")
        data = GetDevices.start()
        return data