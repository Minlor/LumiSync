import random
import time

from lumisync import connection, utils

def fast_change() -> None:
    """Tests the fast change of the LEDs."""
    server, devices = connection.connect()

    # TODO: Tests only one device now, expand this in the future
    device = devices[0]
    try:
        connection.switch_razer(server, device, True)
        for _ in range(100):
            t = time.perf_counter()
            colors = []
            for _ in range(5):
                colors.append(
                    [random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)]
                )

            connection.send_razer_data(server, device, utils.convert_colors(colors))
            time.sleep(0.001)
            print(time.perf_counter() - t)
    finally:
        server.close()

if __name__ == "__main__":
    fast_change()
