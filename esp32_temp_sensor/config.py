import socket

# Configuration settings

# WiFi Settings
WIFI_SSID = "Sibrine!"
WIFI_PASSWORD = "KoningCuba"

# UDP Settings
UDP_TARGET_IP = "192.168.129.168"  # IP of the computer running the receiver
UDP_PORT = 5005
SENSOR_ID = "esp32_pt100_003"

# SPI Pins (ESP32-C3 Super Mini)
SPI_SCK_PIN = 4
SPI_MISO_PIN = 3
SPI_MOSI_PIN = 2
SPI_CS_PIN = 1

# MAX31865 Configuration
# R_REF: The value of the reference resistor on the board (usually 430.0 for PT100)
R_REF = 430.0
# R_NOMINAL: The 'nominal' resistance of the sensor at 0 degrees C (100.0 for PT100)
R_NOMINAL = 100.0
# WIRES: The number of wires for the PT100 sensor (2, 3, or 4)
WIRES = 3
