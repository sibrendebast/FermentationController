# FermController

A fermentation temperature control system for homebrewing, running on Raspberry Pi.

## Features

- **Multi-fermenter support** - Control up to 3 fermenters independently
- **Temperature profiles** - Create and run fermentation schedules with ramping
- **PID or Bang-Bang control** - Choose your control mode
- **PT100 RTD sensors** - Accurate temperature measurement via MAX31865
- **Glycol cooling** - Dynamic glycol setpoint optimization
- **PWA support** - Install on mobile for quick access
- **Temperature logging** - Historical data with graphs

## Hardware

- Raspberry Pi (tested on Pi 4)
- MAX31865 PT100 amplifier boards
- DS18B20 for glycol temperature
- Relay board for heaters, solenoids, pump, chiller

## Setup

```bash
# Clone the repo
git clone https://github.com/sibrendebast/FermentationController.git

# Run install script
./install.sh
```

## Configuration

Edit `app_config.py` to configure:
- GPIO pins
- Sensor IDs
- Control parameters
- PID tuning

## License

MIT