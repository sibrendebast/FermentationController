import network
import time
import config

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    if not wlan.isconnected():
        print('Connecting to network...')
        wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        
        # Wait for connection with timeout
        max_wait = 10
        while max_wait > 0:
            if wlan.status() < 0 or wlan.status() >= 3:
                print("Connected")
                break
            max_wait -= 1
            print('Waiting for connection...')
            time.sleep(1)
            
    if wlan.isconnected():
        print('Network config:', wlan.ifconfig())
        time.sleep(2)
    else:
        print('WiFi Connection failed')
    print("booting.......")

# Disable AP mode
# ap = network.WLAN(network.AP_IF)
# ap.active(False)

connect_wifi()
print("booting...")
time.sleep(1)
