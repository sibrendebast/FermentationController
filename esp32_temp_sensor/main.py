import time
import json
import socket
from machine import Pin, SPI
import config
from lib.max31865 import Max31865

def main():
    print("Starting ESP32 PT100 Sensor (UDP Mode)...")
    
    # Initialize SPI
    spi = SPI(1, baudrate=1000000, polarity=0, phase=1, 
              sck=Pin(config.SPI_SCK_PIN), 
              mosi=Pin(config.SPI_MOSI_PIN), 
              miso=Pin(config.SPI_MISO_PIN))
    
    cs = Pin(config.SPI_CS_PIN, Pin.OUT)
    
    # Initialize Sensor
    sensor = Max31865(spi, cs, wires=3)
    
    # Initialize UDP Socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    print(f"Sending data to {config.UDP_TARGET_IP}:{config.UDP_PORT}")

    while True:
        try:
            # Read Temperature
            temp = sensor.temperature # Changed from sensor.read() to sensor.temperature to match original sensor usage
            print('Temperature: {:0.2f} C'.format(temp))
            
            # Create JSON payload
            payload = json.dumps({
                "sensor_id": config.SENSOR_ID,
                "temperature": round(temp, 2)
            })
            
            # Send via UDP
            sock.sendto(payload.encode(), (config.UDP_TARGET_IP, config.UDP_PORT))
            print(f"Sent: {payload}")
            
        except Exception as e:
            print(f"Error: {e}")
            
        time.sleep(5)

if __name__ == "__main__":
    main()
