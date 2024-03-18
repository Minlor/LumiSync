import time
import colour

#Local
from utils import SendData

print("Attempting to turn on razer mode...")
SendData.send_razer_on_off(True)

blue = [0, 0, 255]
green = [0, 255, 0]
red = [255, 0, 0]

all_colors = [blue, green, red]

colors = [blue]

print("Attempting to send colors...")
print(colors)
SendData.send_razer_data(SendData.convert_colors(colors))
print("Colors sent!")

print("Transitioning to next color in all_colors...")
previous = None
for color in all_colors:
    if previous is None:
        previous = colour.Color(rgb=(color[0]/255, color[1]/255, color[2]/255))
        continue
    color = colour.Color(rgb=(color[0]/255, color[1]/255, color[2]/255))
    range = list(previous.range_to(color, 10))
    for i in range:
        SendData.send_razer_data(SendData.convert_colors([[int(i.red*255), int(i.green*255), int(i.blue*255)]]))
        time.sleep(0.025)
    previous = color








time.sleep(5)
print("Attempting to turn off razer mode, it might take up to a minute for the lights to refresh...")
SendData.send_razer_on_off()

