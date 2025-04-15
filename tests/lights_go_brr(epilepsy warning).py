import random
import time

from lumisync import connection, utils

connection.switch_razer(True)
for x in range(100):
    t = time.perf_counter()
    colors = []
    for i in range(5):
        colors.append(
            [random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)]
        )
    connection.send_razer_data(utils.convert_colors(colors))
    time.sleep(0.001)
    print(time.perf_counter() - t)
