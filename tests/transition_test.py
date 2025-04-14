import time

import colour

from lumisync import connection, utils

print("Attempting to turn on razer mode...")
connection.switch_razer(True)

colors = [utils.get_color("blue")]
all_colors = [utils.get_color(name) for name in ["blue", "green", "red"]]

print("Attempting to send colors...")
print(colors)
connection.send_razer_data(utils.convert_colors(colors))
print("Colors sent!")

print("Transitioning to next color in all_colors...")
previous = None
for color in all_colors:
    if previous is None:
        previous = colour.Color(rgb=(color[0] / 255, color[1] / 255, color[2] / 255))
        continue
    color = colour.Color(rgb=(color[0] / 255, color[1] / 255, color[2] / 255))
    range = list(previous.range_to(color, 10))
    for i in range:
        connection.send_razer_data(
            utils.convert_colors(
                [[int(i.red * 255), int(i.green * 255), int(i.blue * 255)]]
            )
        )
        time.sleep(0.025)
    previous = color


time.sleep(5)
print(
    "Attempting to turn off razer mode, it might take up to a minute for the lights to refresh..."
)
connection.switch_razer()
