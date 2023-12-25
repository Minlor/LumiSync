import socket
import json
import time

multicast = "239.255.255.250"
port = 4001
listen_port = 4002

def start():
    requestScan()
    print("Listening for devices...")
    data = listen()
    print("Device found!")
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
        print("No devices found!")
        exit()
    print("Received message from: ", address)
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
    print("Data written to Settings.json")
    return(data)