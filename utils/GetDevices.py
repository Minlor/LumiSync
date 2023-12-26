import socket
import json
import time
import colorama

multicast = "239.255.255.250"

port = 4001
listen_port = 4002

def start():
    requestScan()
    print(f"{colorama.Fore.YELLOW}Trying to find device...")
    data = listen()
    print(f"{colorama.Fore.GREEN}Device found!")
    new_data = writeJSON(json.loads(data[0]))
    return(new_data)

def requestScan():
    data = {"msg":{"cmd":"scan","data":{"account_topic":"reserve"}}}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(bytes(json.dumps(data), "utf-8"), (multicast, port))
    return

def listen():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    sock.bind(('', listen_port))
    try:
        message, address = sock.recvfrom(1024)
    except socket.timeout:
        print(f"{colorama.Fore.RED}Error: No device found!")
        exit(1)
    print(f"{colorama.Fore.LIGHTGREEN_EX}Received message from: ", address)
    return(message, address)

def writeJSON(data):
    with open("settings.json", "w") as f:
        data = {
            "Model": data["msg"]["data"]["sku"],
            "Device_IP": data["msg"]["data"]["ip"],
            "Device_Port": 4003,
            "Time": time.time()
        }
        json.dump(data, f)
    print(f"{colorama.Fore.LIGHTGREEN_EX}Data written to Settings.json")
    return(data)