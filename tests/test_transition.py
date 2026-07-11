import time

from lumisync import connection, utils


def transition() -> None:
    server, devices = connection.connect()

    # TODO: Tests only one device now, expand this in the future
    device = devices[0]
    try:
        print("Attempting to turn on razer mode...")
        connection.switch_razer(server, device, True)

        colors = [utils.get_color("blue")]
        all_colors = [utils.get_color(name) for name in ["blue", "green", "red"]]

        print("Attempting to send colors...")
        print(colors)
        connection.send_razer_data(server, device, utils.convert_colors(colors))
        print("Colors sent!")

        print("Transitioning to next color in all_colors...")
        previous = None
        for color in all_colors:
            if previous is None:
                previous = color
                continue
            for step in range(1, 11):
                fraction = step / 10
                blended = [
                    int(utils.lerp(previous[channel], color[channel], fraction))
                    for channel in range(3)
                ]
                connection.send_razer_data(
                    server,
                    device,
                    utils.convert_colors([blended]),
                )
                time.sleep(0.025)
            previous = color

        time.sleep(5)
        print(
            "Attempting to turn off razer mode, it might take up to a minute for the lights to refresh..."
        )
        connection.switch_razer(server, device, False)
    finally:
        server.close()

if __name__ == "__main__":
    transition()
