import random
import time

from lumisync import connection, utils

colors = []
for i in range(5):
    colors.append(
        [random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)]
    )

print("Attempting to turn on razer mode...")
connection.switch_razer(True)
print("Attempting to send colors...")
print(colors)
connection.send_razer_data(utils.convert_colors(colors))
print("Colors sent!")
time.sleep(5)
print(
    "Attempting to turn off razer mode, it might take up to a minute for the lights to refresh..."
)
connection.switch_razer()
