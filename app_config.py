# app_config.py

# Define the number of fermenters you have
NUM_FERMENTERS = 3

# GPIO Pin assignments (placeholders - adjust these for your actual setup)
# It's recommended to use BCM numbering
CHILLER_RELAY_PIN = 0  # Example: GPIO 17 for the chiller relay
PUMP_RELAY_PIN = 12
SOLENOID_PINS = [5, 6, 13] # Example: GPIO 27 for Fermenter 1, GPIO 22 for Fermenter 2
HEATER_PINS  = [16, 20, 21] 
# DS18B20 sensor IDs
# GLYCOL_SENSOR_ID remains a DS18B20
GLYCOL_SENSOR_ID = '000000458afe' 

# PT100 Sensor Configuration
# Each entry represents a fermenter sensor in order (Fermenter 1, 2, 3...)
# Format: {'bus': 0, 'device': 0, 'cs_pin': None} 
# If cs_pin is specified (Manual CS), it will be used instead of the native CE line.
PT100_SENSORS = [
    {'bus': 0, 'device': 0, 'cs_pin': None},  # S1: CE0 (GPIO 8)
    {'bus': 0, 'device': 1, 'cs_pin': None},  # S2: CE1 (GPIO 7)
    {'bus': 0, 'device': 0, 'cs_pin': 25}     # S3: Manual CS (GPIO 25)
]

# Pins that need to be initialized as OUTPUT HIGH for SPI Chip Selects
SPI_CS_PINS = [7, 8, 25]

# Control parameters
TEMP_HYSTERESIS = 0.5  # Degrees Celsius for temperature control deadband
DEFAULT_TARGET_GLYCOL_TEMP = 2.0 # Default target temperature for the glycol bath
GLYCOL_TEMP_HYSTERESIS = 1.0 # Degrees Celsius for glycol temperature control deadband
GLYCOL_TARGET_OFFSET = 5.0 # Degrees C cooler than the lowest active fermenter target
READ_INTERVAL_SECONDS = 2 # How often to read sensors and update control (e.g., every 5 seconds)
MIN_GLYCOL_TEMP = -5

# Control mode: "bangbang" (current on/off with hysteresis) or "pid" (PID with duty cycle)
CONTROL_MODE = "bangbang"

# Fermenter-specific configuration
FERMENTER_CONFIG = [
    {"volume_liters": 500, "heater_watts": 200},  # Fermenter 1
    {"volume_liters": 320, "heater_watts": 200},  # Fermenter 2
    {"volume_liters": 320, "heater_watts": 200},  # Fermenter 3
]

# PID controller parameters
PID_PARAMS = {
    "Kp": 20.0,              # Proportional gain
    "Ki": 0.5,               # Integral gain
    "Kd": 5.0,               # Derivative gain
    "duty_cycle_seconds": 60  # On/off cycle period for time-proportioned control
}

# Glycol chiller protection (prevents short cycling)
CHILLER_MIN_ON_TIME = 300   # Minimum on time in seconds (5 minutes)
CHILLER_MIN_OFF_TIME = 180  # Minimum off time in seconds (3 minutes)

# Dynamic glycol setpoint mode: True = adjust based on demand, False = fixed offset
DYNAMIC_GLYCOL_SETPOINT = True

SETTINGS_FILE = 'ferm_settings.json'