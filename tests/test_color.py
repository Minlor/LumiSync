import time

from lumisync import connection, utils


def color() -> None:
    server, devices = connection.connect()

    # TODO: Tests only one device now, expand this in the future
    device = devices[0]
    try:
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
        colors = [utils.get_color(name) for name in names]
        print(colors)

        print("Attempting to turn on razer mode...")
        connection.switch_razer(server, device, True)
        print("Attempting to send colors...")
        print(colors)
        print(utils.convert_colors(colors))

        connection.send_razer_data(server, device, utils.convert_colors(colors))
        print("Colors sent!")
        time.sleep(5)
        print(
            "Attempting to turn off razer mode, it might take up to a minute for the lights to refresh..."
        )

        connection.switch_razer(server, device, False)
    finally:
        server.close()


if __name__ == "__main__":
    color()
