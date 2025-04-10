import time

from lumisync.utils import SendData

blue = [0, 0, 255]
green = [0, 255, 0]
red = [255, 0, 0]
white = [255, 255, 255]
black = [0, 0, 0]
orange = [255, 165, 0]
purple = [128, 0, 128]
yellow = [255, 255, 0]
pink = [255, 192, 203]
aqua = [0, 255, 255]

colors = [blue, green, red, white, black, orange, purple, yellow, pink, aqua]
print(colors)

print("Attempting to turn on razer mode...")
SendData.send_razer_on_off(True)
print("Attempting to send colors...")
print(colors)
print(SendData.convert_colors(colors))
SendData.send_razer_data(SendData.convert_colors(colors))
print("Colors sent!")
time.sleep(5)
print(
    "Attempting to turn off razer mode, it might take up to a minute for the lights to refresh..."
)
SendData.send_razer_on_off()
