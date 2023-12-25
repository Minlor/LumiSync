import random
import time

#Local
from utils import SendData

SendData.send_razer_on_off(True)
for x in range(100):
    t = time.perf_counter()
    colors = []
    for i in range(5):
        colors.append([random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)])
    SendData.send_razer_data(SendData.convert_colors(colors))
    time.sleep(0.001)
    print(time.perf_counter() - t)

