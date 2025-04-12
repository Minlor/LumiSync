import random
import time

# Local
from utils import SendData

colors = []
for i in range(5):
    colors.append(
        [random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)]
    )

print("Attempting to turn on razer mode...")
SendData.send_razer_on_off(True)
print("Attempting to send colors...")
print(colors)
SendData.send_razer_data(SendData.convert_colors(colors))
print("Colors sent!")
time.sleep(5)
print(
    "Attempting to turn off razer mode, it might take up to a minute for the lights to refresh..."
)
SendData.send_razer_on_off()
