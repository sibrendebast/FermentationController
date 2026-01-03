import socket
import json
import config

UDP_IP = "0.0.0.0" # Listen on all interfaces
UDP_PORT = config.UDP_PORT

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

print(f"Listening for UDP packets on port {UDP_PORT}...")

while True:
    data, addr = sock.recvfrom(1024) # buffer size is 1024 bytes
    try:
        message = data.decode()
        json_data = json.loads(message)
        print(f"Received from {addr}: {json_data}")
    except json.JSONDecodeError:
        print(f"Received invalid JSON from {addr}: {data}")
    except Exception as e:
        print(f"Error processing data from {addr}: {e}")
