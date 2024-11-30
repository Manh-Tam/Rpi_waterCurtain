import os
import time
import dbus
import base64
from PIL import Image
from dbus.mainloop.glib import DBusGMainLoop
import gi
gi.require_version("GLib", "2.0")
from gi.repository import GLib
import firebase_admin
from firebase_admin import credentials, db
import RPi.GPIO as GPIO
import subprocess

# Firebase setup
FIREBASE_CRED_PATH = '/home/waterCurtain/watercurtain-39c47-firebase-adminsdk-88xpf-39a646318c.json'
FIREBASE_DB_URL = 'https://watercurtain-39c47-default-rtdb.asia-southeast1.firebasedatabase.app/'

firebase_initialized = False
listener_handle = None

# Pin definitions for 74HC595N
PIN_DATA = 22   # Data pin
PIN_LATCH = 17  # Latch pin
PIN_CLOCK = 27  # Clock pin

# Delay for clock pulse (adjust as needed for your setup)
CLOCK_DELAY_S = 0.000001

# Path to the folder containing decoded images
IMAGE_FOLDER = "decoded_images"

def initialize_firebase():
    """Initialize Firebase connection."""
    global firebase_initialized
    if not firebase_initialized:
        try:
            cred = credentials.Certificate(FIREBASE_CRED_PATH)
            firebase_admin.initialize_app(cred, {
                'databaseURL': FIREBASE_DB_URL
            })
            firebase_initialized = True
            print("Firebase initialized successfully.")
        except Exception as e:
            print(f"Error initializing Firebase: {e}")

def setup_firebase_listener():
    """Set up a Firebase database listener."""
    global listener_handle
    if firebase_initialized:
        ref = db.reference('/Images')

        def listener(event):
            print("Dữ liệu đã thay đổi:")
            print(f"Đường dẫn đầy đủ: {ref.path}{event.path}")
            #print(f"Dữ liệu mới: {event.data}")
            if event.data is not None:
                # Remove old images before saving new ones
                remove_old_images()
                # Process and save the new images from Firebase
                process_images_from_firebase(event.data)

        # Register the listener
        listener_handle = ref.listen(listener)
        print("Firebase listener set up.")

def remove_old_images():
    """
    Removes all the images in the output directory before saving new ones.
    """
    try:
        files_in_directory = os.listdir(IMAGE_FOLDER)
        for file_name in files_in_directory:
            file_path = os.path.join(IMAGE_FOLDER, file_name)
            if os.path.isfile(file_path):
                os.remove(file_path)
                print(f"Removed old image: {file_name}")
    except Exception as e:
        print(f"Error removing old images: {e}")

def remove_firebase_listener():
    """Remove the Firebase listener if active."""
    global listener_handle
    if listener_handle is not None:
        listener_handle.close()
        listener_handle = None
        print("Firebase listener removed.")

def process_images_from_firebase(json_data):
    """
    Decodes Base64-encoded images from Firebase and saves them as image files.
    """
    for key, base64_str in json_data.items():
        if base64_str:
            try:
                base64_str = base64_str.replace("data:image/jpeg;base64,", "")
                img_data = base64.b64decode(base64_str)

                # Determine the filename for the image (e.g., Img1.jpg)
                img_filename = f"{key}.jpg"
                img_path = os.path.join(IMAGE_FOLDER, img_filename)

                # Write the binary data to an image file
                with open(img_path, 'wb') as img_file:
                    img_file.write(img_data)

                print(f"Saved new image: {img_filename}")

                # Convert the saved image to binary and send to shift register
                send_image_to_shift_register(img_path)

            except base64.binascii.Error as b64_err:
                print(f"Error decoding Base64 for {key}: {b64_err}")
            except Exception as e:
                print(f"Unexpected error for {key}: {e}")

def send_image_to_shift_register(image_path):
    """
    Converts the image to binary and sends the data to the 74HC595 shift register.
    :param image_path: Path to the image file.
    """
    try:
        img = Image.open(image_path).convert('L')  # Convert to grayscale
        binary_img = img.point(lambda x: 0 if x < 128 else 1, '1')  # Convert to binary (1-bit per pixel)

        # Extract binary pixel data
        pixels = list(binary_img.getdata())
        width, height = binary_img.size
        rows = [pixels[i * width:(i + 1) * width] for i in range(height)]

        # Send binary data row by row
        for row in reversed(rows):  # Flip vertically to send bottom-to-top
            binary_string = ''.join(str(bit) for bit in reversed(row))  # Flip horizontally
            shift_out(binary_string)
        print(f"Image {image_path} sent successfully.")
    except Exception as e:
        print(f"Error processing {image_path}: {e}")

def shift_out(binary_string):
    """
    Sends binary string data to the 74HC595 shift register.
    :param binary_string: A string containing '0' and '1' representing the pixel data.
    """
    GPIO.output(PIN_LATCH, GPIO.LOW)  # Disable latch

    for bit in binary_string:
        GPIO.output(PIN_CLOCK, GPIO.LOW)  # Prepare to write
        GPIO.output(PIN_DATA, GPIO.HIGH if bit == '1' else GPIO.LOW)
        GPIO.output(PIN_CLOCK, GPIO.HIGH)  # Pulse clock
        time.sleep(CLOCK_DELAY_S)

    GPIO.output(PIN_LATCH, GPIO.HIGH)  # Enable latch to update the shift register output

def wifi_status_changed(*args, **kwargs):
    """Callback function triggered on Wi-Fi status change."""
    bus = dbus.SystemBus()
    proxy = bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager")
    manager = dbus.Interface(proxy, "org.freedesktop.DBus.Properties")
    connectivity = manager.Get("org.freedesktop.NetworkManager", "Connectivity")

    if connectivity == 4:  # NM_CONNECTIVITY_FULL
        print("Wi-Fi is connected.")
        initialize_firebase()
        setup_firebase_listener()
    else:
        print("Wi-Fi is not connected. Removing Firebase listener.")
        remove_firebase_listener()
        
def check_wifi_connection():
    try:
        # Run the `iwgetid` command to check if the Raspberry Pi is connected to a network
        result = subprocess.run(['iwgetid'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        if result.returncode == 0 and result.stdout:
            # Print the connected SSID if found
            ssid = result.stdout.split('ESSID:')[1].strip().strip('"')
            print(f"Connected to WiFi network: {ssid}")
            return True
        else:
            print("Not connected to any WiFi network.")
            return False
    except Exception as e:
        print(f"An error occurred: {e}")
        return False

def main():
    """Main function to listen for Wi-Fi availability changes."""
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    # Connect to the NetworkManager signal for connectivity changes
    bus.add_signal_receiver(
        wifi_status_changed,
        dbus_interface="org.freedesktop.NetworkManager",
        signal_name="StateChanged"
    )

    # GPIO setup
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(PIN_DATA, GPIO.OUT)
    GPIO.setup(PIN_LATCH, GPIO.OUT)
    GPIO.setup(PIN_CLOCK, GPIO.OUT)

    if check_wifi_connection():
        initialize_firebase()
        setup_firebase_listener()

    print("Listening for Wi-Fi connectivity changes...")
    loop = GLib.MainLoop()
    loop.run()

if __name__ == "__main__":
    main()
