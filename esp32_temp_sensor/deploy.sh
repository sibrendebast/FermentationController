#!/bin/bash

PORT="/dev/tty.usbmodem101"

# Check if ampy is installed
if ! command -v ampy &> /dev/null; then
    echo "ampy not found. Installing adafruit-ampy..."
    pip install adafruit-ampy
fi

echo "Deploying files to ESP32 on $PORT..."

# Create directories (ignore errors if they exist)
echo "Creating directories..."
ampy -p $PORT mkdir lib || true


# Copy files
echo "Copying config.py..."
ampy -p $PORT put config.py

echo "Copying boot.py..."
ampy -p $PORT put boot.py

echo "Copying main.py..."
ampy -p $PORT put main.py

echo "Copying lib/max31865.py..."
ampy -p $PORT put lib/max31865.py lib/max31865.py



echo "Deployment complete. Resetting device..."
ampy -p $PORT reset

echo "To monitor output, you can use: screen $PORT 115200"
