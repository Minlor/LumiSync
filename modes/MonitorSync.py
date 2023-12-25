from PIL import Image
import time
import dxcam

from utils import SendData

ss = dxcam.create()

def start():
    SendData.send_razer_on_off(True)
    while True:

        colors = []

        try:
            screen = ss.grab()
            if screen is None:
                continue


            screen = Image.fromarray(screen)
            width, height = screen.size
        except OSError:
            print("Warning: Screenshot failed, trying again...")
            continue

        # TODO combine old and new colors and then add a smooth transition effect
        # TODO possibly add processes to speed this up

        top, bottom = int(height / 4 * 2), int(height / 4 * 3)

        for x in range(4):
            img = screen.crop((int(width/4 * x), 0, int(width/4 * (x+1)), top))
            point = (int(img.size[0]/2), int(img.size[1]/2))
            colors.append(img.getpixel(point))
        colors.reverse()
        img = screen.crop((0, top, int(width/4), bottom))
        point = (int(img.size[0]/2), int(img.size[1]/2))
        colors.append(img.getpixel(point))
        for x in range(4):
            img = screen.crop((int(width/4 * x), bottom, int(width/4 * (x+1)), height))
            point = (int(img.size[0]/2), int(img.size[1]/2))
            colors.append(img.getpixel(point))
        img = screen.crop((int((width/4 * 3)), top, width, bottom))
        colors.append(img.getpixel(point))
        time.sleep(0.025) # Added to not kill peoples CPUs

        SendData.send_razer_data(SendData.convert_colors(colors))