import time

from lumisync import connection, utils


names = [
    "blue",
    "green",
    "red",
    "white",
    "black",
    "orange",
    "purple",
    "yellow",
    "pink",
    "aqua",
]
colors = values = [utils.get_color(name) for name in names]
print(colors)

print("Attempting to turn on razer mode...")
connection.switch_razer(True)
print("Attempting to send colors...")
print(colors)
print(utils.convert_colors(colors))
connection.send_razer_data(utils.convert_colors(colors))
print("Colors sent!")
time.sleep(5)
print(
    "Attempting to turn off razer mode, it might take up to a minute for the lights to refresh..."
)
connection.switch_razer()
