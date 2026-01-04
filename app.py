import threading
import time
import spidev
import RPi.GPIO as GPIO
from w1thermsensor import W1ThermSensor
import json
import collections
import math
import sys
from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta, timezone

from app_config import *
from database import init_db, log_temperature, get_temperature_logs_for_fermenter, cleanup_old_logs
from profiles import (load_profiles, save_profiles, get_profile_by_id, create_profile, 
                      update_profile, delete_profile, calculate_current_step, get_profile_total_duration)
from pid_controller import (PIDController, DutyCycleManager, ChillerController, 
                            pid_output_to_duty_cycles, calculate_dynamic_glycol_target)
# Store current temperatures and target temperatures

class Max31865Pi:
    def __init__(self, bus, device, cs_pin=None, rtd_nominal=100.0, ref_resistor=430.0):
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = 500000
        self.spi.mode = 1
        self.cs_pin = cs_pin
        self.is_manual = cs_pin is not None 
        self.rtd_nominal = rtd_nominal
        self.ref_resistor = ref_resistor
        self.rtd_nominal = rtd_nominal
        self.ref_resistor = ref_resistor
        self._write_u8(0x00, 0x11 | 0x82) # 50Hz filter, Vbias, Manual/3-wire specific bits if needed
        self.history = collections.deque(maxlen=10) # Moving average buffer

    def _write_u8(self, reg, val):
        if self.is_manual:
            GPIO.output(8, GPIO.HIGH) # Ensure CE0 is quiet
            GPIO.output(7, GPIO.HIGH) # Ensure CE1 is quiet
            GPIO.output(self.cs_pin, GPIO.LOW)
        self.spi.xfer2([reg | 0x80, val])
        if self.is_manual:
            GPIO.output(self.cs_pin, GPIO.HIGH)

    def _read_u16(self, reg):
        if self.is_manual:
            GPIO.output(8, GPIO.HIGH)
            GPIO.output(7, GPIO.HIGH)
            GPIO.output(self.cs_pin, GPIO.LOW)
        resp = self.spi.xfer2([reg & 0x7F, 0x00, 0x00])
        if self.is_manual:
            GPIO.output(self.cs_pin, GPIO.HIGH)
        return (resp[1] << 8) | resp[2]

    def get_reading(self):
        """Returns (Temperature_or_None, Status_String)"""
        # 1. Wake up and clear faults
        config = 0x11 | 0x82 
        self._write_u8(0x00, config)
        time.sleep(0.01)
        
        # 2. Trigger conversion
        self._write_u8(0x00, config | 0x20)
        time.sleep(0.07)
        
        # 3. Read raw data
        raw = self._read_u16(0x01)
        
        # Check the Fault bit (Bit 0 of the RTD LSB)
        has_fault = raw & 0x01
        raw_rtd = raw >> 1
        
        # 4. Calculate Resistance
        res = (raw_rtd / 32768.0) * self.ref_resistor
        
        # 5. Logic Check for Disconnection
        # 0 Ohms = Short/Disconnected, 430+ Ohms = Open Circuit
        if has_fault or res < 10.0 or res > 400.0:
            return None, "NOT CONNECTED"

        # 6. Math calculation
        try:
            A, B = 3.9083e-3, -5.775e-7
            Z1, Z2 = -A, (A * A - 4 * B)
            Z3, Z4 = (4 * B / self.rtd_nominal), (2 * B)
            temp = (math.sqrt(Z2 + (Z3 * res)) + Z1) / Z4
            
            # --- Apply Moving Average Filter ---
            self.history.append(temp)
            avg_temp = sum(self.history) / len(self.history)
            
            return avg_temp, "OK"
        except Exception:
            return None, "MATH ERROR"

# --- Global State ---
# Store current temperatures and target temperatures
current_temperatures = {
    "glycol_bath": 0.0,
    "fermenters": [20.0] * NUM_FERMENTERS,
}
target_temperatures = {
    "glycol_bath": DEFAULT_TARGET_GLYCOL_TEMP,
    "fermenters": [18.0] * NUM_FERMENTERS # Default target temperature for fermenters
}

# Control states
chiller_on = False
pump_on = False
solenoid_states = [False] * NUM_FERMENTERS # True if valve is open (cooling), False if closed
heater_states = [False] * NUM_FERMENTERS   # True if heater is on, False if off
fermenter_active_status = [True] * NUM_FERMENTERS # True if fermenter is in use, False otherwise

# Profile assignment state
fermenter_profiles = [None] * NUM_FERMENTERS  # Profile ID assigned to each fermenter
fermenter_profile_start_times = [None] * NUM_FERMENTERS  # ISO timestamp when profile started
fermenter_current_step = [0] * NUM_FERMENTERS  # Current step index in the profile
fermenter_profile_offsets = [0.0] * NUM_FERMENTERS  # Manual temperature offset when profile is active
pid_outputs = [0.0] * NUM_FERMENTERS  # Current PID output (-100 to 100)

# Control mode - can be changed at runtime
control_mode = CONTROL_MODE  # Initialize from config, can be "bangbang" or "pid"

app = Flask(__name__)

# --- Hardware Abstraction Layer (HAL) ---

def setup_gpio():
    """
    Initializes GPIO pins.
    """
    print("HW: Attempting to set up GPIO for hardware...")
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(CHILLER_RELAY_PIN, GPIO.OUT)
        GPIO.output(CHILLER_RELAY_PIN, GPIO.HIGH)
        
        GPIO.setup(PUMP_RELAY_PIN, GPIO.OUT)
        GPIO.output(PUMP_RELAY_PIN, GPIO.HIGH)
        for pin in SOLENOID_PINS:
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.HIGH)
            
        for pin in HEATER_PINS:
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.HIGH) # Assuming Active LOW relay
        
        # Setup SPI CS pins
        for pin in SPI_CS_PINS:
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.HIGH)
            
        print("GPIO pins initialized.")
    except (ImportError, RuntimeError):
        print("ERROR: RPi.GPIO not found or not running on a Raspberry Pi.")
        exit(1)
    except Exception as e:
        print(f"Error setting up GPIO: {e}")
        exit(1)

def set_chiller_state(state):
    """
    Controls the glycol chiller relay.
    `state` is True for ON, False for OFF.
    """
    global chiller_on
    chiller_on = state
    print(f"Chiller set to: {'ON' if state else 'OFF'}")

    try:
        GPIO.output(CHILLER_RELAY_PIN, GPIO.LOW if state else GPIO.HIGH) # Assuming active LOW relay
    except Exception as e:
        print(f"Error controlling chiller: {e}")

def set_pump_state(state):
    """
    Controls the glycol pump relay.
    `state` is True for ON, False for OFF.
    """
    global pump_on
    pump_on = state
    print(f"Pump set to: {'ON' if state else 'OFF'}")

    try:
        GPIO.output(PUMP_RELAY_PIN, GPIO.LOW if state else GPIO.HIGH) # Assuming active LOW relay
    except Exception as e:
        print(f"Error controlling pump: {e}")

def set_solenoid_state(fermenter_index, state):
    """
    Controls a specific fermenter's solenoid valve.
    `state` is True for OPEN (cooling), False for CLOSED.
    """
    global solenoid_states
    if 0 <= fermenter_index < NUM_FERMENTERS:
        solenoid_states[fermenter_index] = state

        try:
            GPIO.output(SOLENOID_PINS[fermenter_index], GPIO.LOW if state else GPIO.HIGH) # Assuming active LOW relay
        except Exception as e:
            print(f"Error controlling solenoid for fermenter {fermenter_index + 1}: {e}")

def set_heater_state(fermenter_index, state):
    """
    Controls a specific fermenter's heater.
    `state` is True for ON (heating), False for OFF.
    """
    global heater_states
    if 0 <= fermenter_index < NUM_FERMENTERS:
        heater_states[fermenter_index] = state

        try:
            GPIO.output(HEATER_PINS[fermenter_index], GPIO.LOW if state else GPIO.HIGH) # Assuming active LOW relay
        except Exception as e:
            print(f"Error controlling heater for fermenter {fermenter_index + 1}: {e}")

def read_ds18b20_temperature(sensor_id):
    """
    Reads temperature from a DS18B20 sensor.
    Returns temperature in Celsius or None if reading fails.
    """
    # --- Real Hardware ---
    try:
        if W1ThermSensor is None:
            print("ERROR: W1ThermSensor library not available for hardware mode.")
            return None
        sensor = W1ThermSensor(sensor_id=sensor_id)
        temp_c = sensor.get_temperature()
        return temp_c
    except Exception:  # Catch all exceptions, including SensorNotReadyError
        return None

# --- Settings Persistence ---
def load_settings():
    """Loads settings from the JSON file."""
    global target_temperatures, fermenter_active_status, fermenter_profiles, fermenter_profile_start_times, fermenter_current_step, fermenter_profile_offsets, control_mode
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)

            loaded_targets = settings.get("target_fermenters")
            if isinstance(loaded_targets, list) and len(loaded_targets) == NUM_FERMENTERS:
                target_temperatures["fermenters"] = loaded_targets
                print(f"Loaded target_fermenters from {SETTINGS_FILE}")
            elif loaded_targets is not None: # Key exists but is invalid (e.g., wrong length)
                print(f"Warning: 'target_fermenters' in {SETTINGS_FILE} is invalid or length mismatch. Using defaults.")

            loaded_status = settings.get("fermenter_active_status")
            if isinstance(loaded_status, list) and len(loaded_status) == NUM_FERMENTERS:
                if all(isinstance(item, bool) for item in loaded_status):
                    fermenter_active_status = loaded_status
                    print(f"Loaded fermenter_active_status from {SETTINGS_FILE}")
                else:
                    print(f"Warning: 'fermenter_active_status' in {SETTINGS_FILE} contains non-boolean values. Using defaults.")
            elif loaded_status is not None: # Key exists but is invalid
                print(f"Warning: 'fermenter_active_status' in {SETTINGS_FILE} is invalid or length mismatch. Using defaults.")

            # Load profile assignments
            loaded_profiles = settings.get("fermenter_profiles")
            if isinstance(loaded_profiles, list) and len(loaded_profiles) == NUM_FERMENTERS:
                fermenter_profiles = loaded_profiles
                print(f"Loaded fermenter_profiles from {SETTINGS_FILE}")
            
            loaded_start_times = settings.get("fermenter_profile_start_times")
            if isinstance(loaded_start_times, list) and len(loaded_start_times) == NUM_FERMENTERS:
                fermenter_profile_start_times = loaded_start_times
                print(f"Loaded fermenter_profile_start_times from {SETTINGS_FILE}")
            
            loaded_offsets = settings.get("fermenter_profile_offsets")
            if isinstance(loaded_offsets, list) and len(loaded_offsets) == NUM_FERMENTERS:
                fermenter_profile_offsets = loaded_offsets
                print(f"Loaded fermenter_profile_offsets from {SETTINGS_FILE}")
            
            loaded_current_step = settings.get("fermenter_current_step")
            if isinstance(loaded_current_step, list) and len(loaded_current_step) == NUM_FERMENTERS:
                fermenter_current_step = loaded_current_step
                print(f"Loaded fermenter_current_step from {SETTINGS_FILE}")
            
            loaded_control_mode = settings.get("control_mode")
            if loaded_control_mode in ["bangbang", "pid"]:
                control_mode = loaded_control_mode
                print(f"Loaded control_mode: {control_mode}")

    except FileNotFoundError:
        print(f"{SETTINGS_FILE} not found. Using default settings. File will be created on first setting change.")
    except json.JSONDecodeError:
        print(f"Error decoding JSON from {SETTINGS_FILE}. Using default settings.")
    except Exception as e:
        print(f"Error loading settings: {e}. Using default settings.")

def save_settings():
    """Saves current settings to the JSON file."""
    settings_to_save = {
        "target_fermenters": target_temperatures["fermenters"],
        "fermenter_active_status": fermenter_active_status,
        "fermenter_profiles": fermenter_profiles,
        "fermenter_profile_start_times": fermenter_profile_start_times,
        "fermenter_current_step": fermenter_current_step,
        "fermenter_profile_offsets": fermenter_profile_offsets,
        "control_mode": control_mode
    }
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings_to_save, f, indent=4)
        print(f"Settings saved to {SETTINGS_FILE}")
    except IOError as e:
        print(f"Error saving settings to {SETTINGS_FILE}: {e}")
    except Exception as e:
        print(f"An unexpected error occurred while saving settings: {e}")



# --- Control Logic ---

# Global PID controllers and duty cycle managers (initialized once)
pid_controllers = [PIDController(PID_PARAMS["Kp"], PID_PARAMS["Ki"], PID_PARAMS["Kd"]) 
                   for _ in range(NUM_FERMENTERS)]
duty_cycle_managers = [DutyCycleManager(PID_PARAMS["duty_cycle_seconds"]) 
                       for _ in range(NUM_FERMENTERS)]
chiller_controller = ChillerController(CHILLER_MIN_ON_TIME, CHILLER_MIN_OFF_TIME)

# Store duty cycles for API/display
fermenter_heating_duty = [0.0] * NUM_FERMENTERS
fermenter_cooling_duty = [0.0] * NUM_FERMENTERS


def control_loop():
    """
    Unified control loop that runs in a separate thread.
    Checks control_mode each iteration, allowing runtime switching.
    Supports both 'bangbang' and 'pid' modes.
    """
    global control_mode
    
    # Initialize PT100 sensors once
    pt100_sensors = []
    print("Initializing PT100 sensors...")
    for conf in PT100_SENSORS:
        try:
            sensor = Max31865Pi(conf['bus'], conf['device'], cs_pin=conf.get('cs_pin'))
            pt100_sensors.append(sensor)
        except Exception as e:
            print(f"Error initializing PT100 sensor {conf}: {e}")
            pt100_sensors.append(None)

    print("Unified control loop started.")
    last_mode = None
    
    while True:
        current_time = datetime.now(timezone.utc)
        
        # Log mode change
        if control_mode != last_mode:
            print(f"Control mode: {control_mode}")
            last_mode = control_mode
        
        # --- Read All Temperatures ---
        current_temperatures["glycol_bath"] = read_ds18b20_temperature(GLYCOL_SENSOR_ID)
        
        for i in range(NUM_FERMENTERS):
            if i < len(pt100_sensors) and pt100_sensors[i]:
                temp, status = pt100_sensors[i].get_reading()
                if status == "OK" and temp is not None:
                    current_temperatures["fermenters"][i] = round(temp, 2)
                    log_temperature(i, current_time, temp)
                else:
                    current_temperatures["fermenters"][i] = None
                    fermenter_active_status[i] = False
            else:
                current_temperatures["fermenters"][i] = None
                fermenter_active_status[i] = False
        
        # --- Calculate Target Temperatures (shared by both modes) ---
        active_targets = []
        cooling_duties = []  # For PID mode glycol optimization
        
        for i in range(NUM_FERMENTERS):
            current_f_temp = current_temperatures["fermenters"][i]
            
            # Skip inactive or failed fermenters
            if not fermenter_active_status[i] or current_f_temp is None:
                set_heater_state(i, False)
                set_solenoid_state(i, False)
                if control_mode == "pid":
                    pid_controllers[i].reset()
                    fermenter_heating_duty[i] = 0
                    fermenter_cooling_duty[i] = 0
                cooling_duties.append(0)
                continue
            
            # Get target temperature (from profile or manual)
            target_f_temp = target_temperatures["fermenters"][i]
            
            # Apply profile if active
            if fermenter_profiles[i] and fermenter_profile_start_times[i]:
                profile = get_profile_by_id(fermenter_profiles[i])
                if profile:
                    step_idx, profile_target, time_remaining = calculate_current_step(
                        profile, fermenter_profile_start_times[i]
                    )
                    if profile_target is not None:
                        # Reset offset on step change
                        if step_idx is not None and step_idx != fermenter_current_step[i]:
                            if fermenter_profile_offsets[i] != 0.0:
                                print(f"Fermenter {i+1} step changed, resetting offset")
                            fermenter_profile_offsets[i] = 0.0
                        
                        target_f_temp = profile_target + fermenter_profile_offsets[i]
                        target_temperatures["fermenters"][i] = target_f_temp
                        fermenter_current_step[i] = step_idx if step_idx is not None else 0
            
            active_targets.append(target_f_temp)
            
            # Check if glycol is available for cooling
            glycol_temp = current_temperatures["glycol_bath"]
            can_cool = glycol_temp is not None and glycol_temp < current_f_temp
            
            # --- Apply Control Based on Mode ---
            if control_mode == "pid":
                # PID Control with time-proportioned output
                pid_output = pid_controllers[i].compute(target_f_temp, current_f_temp)
                pid_outputs[i] = pid_output
                heating_duty, cooling_duty = pid_output_to_duty_cycles(pid_output)
                
                fermenter_heating_duty[i] = heating_duty
                fermenter_cooling_duty[i] = cooling_duty
                cooling_duties.append(cooling_duty)
                
                if cooling_duty > 0 and can_cool:
                    should_cool = duty_cycle_managers[i].should_be_on(cooling_duty)
                    set_solenoid_state(i, should_cool)
                    set_heater_state(i, False)
                elif heating_duty > 0:
                    should_heat = duty_cycle_managers[i].should_be_on(heating_duty)
                    set_heater_state(i, should_heat)
                    set_solenoid_state(i, False)
                else:
                    set_heater_state(i, False)
                    set_solenoid_state(i, False)
            else:
                # Bang-Bang Control with hysteresis
                cooling_duties.append(0)  # Not used in bangbang mode
                
                # Cooling Logic
                if current_f_temp > target_f_temp + TEMP_HYSTERESIS and can_cool:
                    if not solenoid_states[i]:
                        print(f"Fermenter {i+1} Cooling ON (T={current_f_temp:.2f}, Target={target_f_temp})")
                    set_solenoid_state(i, True)
                    set_heater_state(i, False)
                elif current_f_temp < target_f_temp:
                    if solenoid_states[i]:
                        print(f"Fermenter {i+1} Cooling OFF (T={current_f_temp:.2f}, Target={target_f_temp})")
                    set_solenoid_state(i, False)
                
                # Heating Logic
                if current_f_temp < target_f_temp - TEMP_HYSTERESIS:
                    if not heater_states[i]:
                        print(f"Fermenter {i+1} Heating ON (T={current_f_temp:.2f}, Target={target_f_temp})")
                    set_heater_state(i, True)
                    set_solenoid_state(i, False)
                elif current_f_temp > target_f_temp:
                    if heater_states[i]:
                        print(f"Fermenter {i+1} Heating OFF (T={current_f_temp:.2f}, Target={target_f_temp})")
                    set_heater_state(i, False)
        
        # --- Pump Control ---
        if any(solenoid_states):
            set_pump_state(True)
        else:
            set_pump_state(False)
        
        # --- Glycol Setpoint ---
        if control_mode == "pid" and DYNAMIC_GLYCOL_SETPOINT and cooling_duties:
            glycol_target = calculate_dynamic_glycol_target(
                cooling_duties, active_targets, MIN_GLYCOL_TEMP, GLYCOL_TARGET_OFFSET
            )
            target_temperatures["glycol_bath"] = glycol_target
        else:
            # Fixed offset mode
            if active_targets:
                target_temperatures["glycol_bath"] = max(min(active_targets) - GLYCOL_TARGET_OFFSET, MIN_GLYCOL_TEMP)
            else:
                target_temperatures["glycol_bath"] = None
        
        # --- Chiller Control ---
        glycol_temp = current_temperatures["glycol_bath"]
        glycol_target = target_temperatures["glycol_bath"]
        
        if glycol_temp is not None and glycol_target is not None:
            if control_mode == "pid":
                # Use chiller controller with minimum cycle times
                should_chill = chiller_controller.should_turn_on(glycol_temp, glycol_target, GLYCOL_TEMP_HYSTERESIS)
                set_chiller_state(should_chill)
            else:
                # Simple bang-bang chiller control
                if glycol_temp > glycol_target + GLYCOL_TEMP_HYSTERESIS:
                    if not chiller_on:
                        set_chiller_state(True)
                elif glycol_temp < glycol_target - GLYCOL_TEMP_HYSTERESIS:
                    if chiller_on:
                        set_chiller_state(False)
        else:
            set_chiller_state(False)
        
        time.sleep(READ_INTERVAL_SECONDS)


# --- Flask Routes ---

@app.route('/')
def index():
    """Serves the main HTML page."""
    return render_template('index.html',
                           num_fermenters=NUM_FERMENTERS,
                           initial_target_glycol_temp=target_temperatures["glycol_bath"],
                           read_interval_seconds=READ_INTERVAL_SECONDS)

@app.route('/settings')
def settings_page():
    """Serves the settings page."""
    return render_template('settings.html')


@app.route('/api/control_mode', methods=['GET', 'POST'])
def manage_control_mode():
    """
    GET: Returns current control mode.
    POST: Changes control mode at runtime (no restart needed).
    """
    global control_mode
    
    if request.method == 'GET':
        return jsonify({"control_mode": control_mode})
    
    if request.method == 'POST':
        data = request.get_json()
        if not data or 'control_mode' not in data:
            return jsonify({"error": "Missing control_mode"}), 400
        
        new_mode = data['control_mode']
        if new_mode not in ['bangbang', 'pid']:
            return jsonify({"error": "Invalid control_mode. Must be 'bangbang' or 'pid'."}), 400
        
        old_mode = control_mode
        control_mode = new_mode
        save_settings()
        
        return jsonify({
            "status": "success",
            "message": f"Control mode changed from {old_mode} to {new_mode}",
            "control_mode": control_mode
        })

@app.route('/api/temperatures', methods=['GET'])
def get_temperatures():
    """Returns current temperatures and control states as JSON."""
    
    # Build profile info for each fermenter
    profile_info = []
    for i in range(NUM_FERMENTERS):
        if fermenter_profiles[i]:
            profile = get_profile_by_id(fermenter_profiles[i])
            if profile and fermenter_profile_start_times[i]:
                step_idx, target, time_remaining = calculate_current_step(
                    profile, fermenter_profile_start_times[i]
                )
                current_step_name = profile['steps'][step_idx]['name'] if step_idx is not None and step_idx < len(profile['steps']) else None
                profile_info.append({
                    "profile_id": fermenter_profiles[i],
                    "profile_name": profile['name'],
                    "current_step": step_idx,
                    "current_step_name": current_step_name,
                    "time_remaining_hours": round(time_remaining, 1) if time_remaining else None,
                    "start_time": fermenter_profile_start_times[i],
                    "offset": fermenter_profile_offsets[i],
                    "profile_target": target  # The raw profile target before offset
                })
            else:
                profile_info.append(None)
        else:
            profile_info.append(None)
    
    response = {
        "glycol_bath": current_temperatures["glycol_bath"],
        "target_glycol_bath": target_temperatures["glycol_bath"],
        "fermenters": current_temperatures["fermenters"], # This will contain None for disconnected sensors
        "target_fermenters": target_temperatures["fermenters"],
        "control_mode": control_mode,
        "pid_outputs": pid_outputs,
        "chiller_on": chiller_on,
        "pump_on": pump_on,
        "solenoid_states": solenoid_states,
        "heater_states": heater_states,
        "fermenter_active_status": [status if current_temperatures["fermenters"][i] is not None else False for i, status in enumerate(fermenter_active_status)],
        "fermenter_profiles": profile_info,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    return jsonify(response)

@app.route('/api/set_target', methods=['POST'])
def set_target():
    """
    Sets target temperatures for fermenters.
    Expects JSON data like: {"fermenter_index": 0, "target_temp": 19.5}
    """
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON data received"}), 400

    fermenter_index = data.get('fermenter_index')
    target_temp = data.get('target_temp')

    if fermenter_index is None or target_temp is None:
        return jsonify({"status": "error", "message": "Missing fermenter_index or target_temp"}), 400

    try:
        fermenter_index = int(fermenter_index)
        target_temp = float(target_temp)

        if not (0 <= fermenter_index < NUM_FERMENTERS):
            return jsonify({"status": "error", "message": f"Invalid fermenter_index. Must be between 0 and {NUM_FERMENTERS - 1}."}), 400

        # Check if a profile is active for this fermenter
        if fermenter_profiles[fermenter_index] and fermenter_profile_start_times[fermenter_index]:
            # Profile is active - calculate and update the offset
            profile = get_profile_by_id(fermenter_profiles[fermenter_index])
            if profile:
                step_idx, profile_target, _ = calculate_current_step(
                    profile, fermenter_profile_start_times[fermenter_index]
                )
                if profile_target is not None:
                    # Calculate the offset from the profile target
                    new_offset = target_temp - profile_target
                    fermenter_profile_offsets[fermenter_index] = new_offset
                    target_temperatures["fermenters"][fermenter_index] = target_temp
                    save_settings()
                    print(f"Updated Fermenter {fermenter_index + 1} profile offset to: {new_offset:+.1f}°C (target: {target_temp}°C)")
                    return jsonify({"status": "success", "message": f"Profile offset updated to {new_offset:+.1f}°C"})
        
        # No profile active - set manual target as normal
        target_temperatures["fermenters"][fermenter_index] = target_temp
        save_settings()
        print(f"Updated Fermenter {fermenter_index + 1} target to: {target_temp}°C")
        return jsonify({"status": "success", "message": "Target temperature updated."})
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid data type for fermenter_index or target_temp"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/set_fermenter_status', methods=['POST'])
def set_fermenter_status_route():
    """
    Sets the active status for a fermenter.
    Expects JSON data like: {"fermenter_index": 0, "is_active": true}
    """
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON data received"}), 400

    fermenter_index = data.get('fermenter_index')
    is_active = data.get('is_active')

    if fermenter_index is None or is_active is None:
        return jsonify({"status": "error", "message": "Missing fermenter_index or is_active"}), 400

    if not isinstance(is_active, bool):
        return jsonify({"status": "error", "message": "is_active must be a boolean"}), 400

    try:
        fermenter_index = int(fermenter_index)
        if not (0 <= fermenter_index < NUM_FERMENTERS):
            return jsonify({"status": "error", "message": f"Invalid fermenter_index. Must be between 0 and {NUM_FERMENTERS - 1}."}), 400

        fermenter_active_status[fermenter_index] = is_active
        save_settings() # Save settings after successful update
        print(f"Fermenter {fermenter_index + 1} active status set to: {is_active}")
        # If made inactive, ensure outputs are off
        if not is_active:
            if solenoid_states[fermenter_index]:
                set_solenoid_state(fermenter_index, False)
            if heater_states[fermenter_index]:
                set_heater_state(fermenter_index, False)
        return jsonify({"status": "success", "message": f"Fermenter {fermenter_index + 1} active status updated."})
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid data type for fermenter_index"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/graph')
def graph_page():
    """Serves the graph page."""
    return render_template('graph.html', num_fermenters=NUM_FERMENTERS)

@app.route('/api/temperature_log/<int:fermenter_index>', methods=['GET'])
def get_temperature_log(fermenter_index):
    """
    Returns the temperature log for a specific fermenter.
    Accepts 'start' and 'end' query parameters (ISO format) to specify the time range.
    e.g., /api/temperature_log/0?start=2025-12-01T00:00:00Z&end=2025-12-28T23:59:59Z
    For backwards compatibility, also accepts 'days' parameter.
    """
    if not (0 <= fermenter_index < NUM_FERMENTERS):
        return jsonify({"status": "error", "message": f"Invalid fermenter_index. Must be between 0 and {NUM_FERMENTERS - 1}."}), 400

    start_date = request.args.get('start')
    end_date = request.args.get('end')
    days_str = request.args.get('days')

    # If days is provided (backwards compatibility), convert to start/end
    if days_str and not start_date:
        try:
            days = int(days_str)
            from datetime import datetime, timedelta, timezone
            end_date = datetime.now(timezone.utc).isoformat()
            start_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        except ValueError:
            return jsonify({"status": "error", "message": "Invalid 'days' parameter. Must be an integer."}), 400

    logs = get_temperature_logs_for_fermenter(fermenter_index, start_date=start_date, end_date=end_date)
    return jsonify(logs)

# --- Profile Routes ---

@app.route('/profiles')
def profiles_page():
    """Serves the profiles management page."""
    return render_template('profiles.html')

@app.route('/api/profiles', methods=['GET', 'POST'])
def manage_profiles_list():
    """
    GET: Returns all profiles.
    POST: Creates a new profile.
    """
    if request.method == 'GET':
        return jsonify(load_profiles())
    
    if request.method == 'POST':
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data received"}), 400
        
        name = data.get('name', '').strip()
        description = data.get('description', '').strip()
        steps = data.get('steps', [])
        
        if not name:
            return jsonify({"status": "error", "message": "Profile name is required"}), 400
        
        new_profile = create_profile(name, description, steps)
        return jsonify(new_profile), 201

@app.route('/api/profiles/<profile_id>', methods=['GET', 'PUT', 'DELETE'])
def manage_single_profile(profile_id):
    """
    GET: Returns a single profile.
    PUT: Updates a profile.
    DELETE: Deletes a profile.
    """
    if request.method == 'GET':
        profile = get_profile_by_id(profile_id)
        if profile:
            return jsonify(profile)
        return jsonify({"status": "error", "message": "Profile not found"}), 404
    
    if request.method == 'PUT':
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data received"}), 400
        
        name = data.get('name', '').strip()
        description = data.get('description', '').strip()
        steps = data.get('steps', [])
        
        updated = update_profile(profile_id, name, description, steps)
        if updated:
            return jsonify(updated)
        return jsonify({"status": "error", "message": "Profile not found"}), 404
    
    if request.method == 'DELETE':
        delete_profile(profile_id)
        return jsonify({"status": "success", "message": "Profile deleted"})

@app.route('/api/fermenter/<int:fermenter_index>/profile', methods=['GET', 'POST', 'DELETE'])
def manage_fermenter_profile(fermenter_index):
    """
    GET: Returns the current profile assignment for a fermenter.
    POST: Assigns a profile to a fermenter and starts it.
    DELETE: Stops the current profile.
    """
    global fermenter_profiles, fermenter_profile_start_times, fermenter_current_step
    
    if not (0 <= fermenter_index < NUM_FERMENTERS):
        return jsonify({"status": "error", "message": f"Invalid fermenter_index"}), 400
    
    if request.method == 'GET':
        profile_id = fermenter_profiles[fermenter_index]
        profile = get_profile_by_id(profile_id) if profile_id else None
        start_time = fermenter_profile_start_times[fermenter_index]
        
        response = {
            "profile_id": profile_id,
            "profile": profile,
            "start_time": start_time,
            "current_step": fermenter_current_step[fermenter_index]
        }
        
        if profile and start_time:
            step_idx, target_temp, time_remaining = calculate_current_step(profile, start_time)
            response["calculated_step"] = step_idx
            response["calculated_target"] = target_temp
            response["time_remaining_hours"] = time_remaining
        
        return jsonify(response)
    
    if request.method == 'POST':
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data received"}), 400
        
        profile_id = data.get('profile_id')
        profile = get_profile_by_id(profile_id)
        
        if not profile:
            return jsonify({"status": "error", "message": "Profile not found"}), 404
        
        # Assign and start the profile
        fermenter_profiles[fermenter_index] = profile_id
        fermenter_profile_start_times[fermenter_index] = datetime.now(timezone.utc).isoformat()
        fermenter_current_step[fermenter_index] = 0
        fermenter_profile_offsets[fermenter_index] = 0.0  # Reset offset when starting a profile
        fermenter_active_status[fermenter_index] = True
        
        save_settings()
        
        return jsonify({
            "status": "success", 
            "message": f"Profile '{profile['name']}' started on Fermenter {fermenter_index + 1}"
        })
    
    if request.method == 'DELETE':
        fermenter_profiles[fermenter_index] = None
        fermenter_profile_start_times[fermenter_index] = None
        fermenter_current_step[fermenter_index] = 0
        fermenter_profile_offsets[fermenter_index] = 0.0  # Reset offset when stopping a profile
        fermenter_active_status[fermenter_index] = False  # Set fermenter to inactive
        
        save_settings()
        
        return jsonify({
            "status": "success", 
            "message": f"Profile stopped on Fermenter {fermenter_index + 1}"
        })


@app.route('/api/fermenter/<int:fermenter_index>/profile/skip', methods=['POST'])
def skip_profile_step(fermenter_index):
    """
    Skips to the next step in the active profile for a fermenter.
    Works by adjusting the start time so we're at the beginning of the next step.
    """
    if not (0 <= fermenter_index < NUM_FERMENTERS):
        return jsonify({"status": "error", "message": "Invalid fermenter index"}), 400
    
    if not fermenter_profiles[fermenter_index] or not fermenter_profile_start_times[fermenter_index]:
        return jsonify({"status": "error", "message": "No active profile on this fermenter"}), 400
    
    profile = get_profile_by_id(fermenter_profiles[fermenter_index])
    if not profile:
        return jsonify({"status": "error", "message": "Profile not found"}), 404
    
    steps = profile.get('steps', [])
    current_step_idx = fermenter_current_step[fermenter_index]
    
    # Check if we're already on the last step
    if current_step_idx >= len(steps) - 1:
        return jsonify({"status": "error", "message": "Already on the last step"}), 400
    
    # Calculate the cumulative time to the START of the next step
    cumulative_hours = 0
    for i in range(current_step_idx + 1):
        step = steps[i]
        step_duration = step.get('duration_hours', 0)
        ramp_hours = step.get('ramp_hours', 0)
        cumulative_hours += step_duration + ramp_hours
    
    # Adjust start time so we're now at the beginning of the next step
    # new_start = now - cumulative_hours
    from datetime import timedelta
    new_start_time = datetime.now(timezone.utc) - timedelta(hours=cumulative_hours)
    fermenter_profile_start_times[fermenter_index] = new_start_time.isoformat()
    fermenter_profile_offsets[fermenter_index] = 0.0  # Reset offset when skipping
    
    save_settings()
    
    next_step_name = steps[current_step_idx + 1]['name'] if current_step_idx + 1 < len(steps) else "End"
    print(f"Fermenter {fermenter_index + 1}: Skipped to step '{next_step_name}'")
    
    return jsonify({
        "status": "success",
        "message": f"Skipped to step: {next_step_name}"
    })


@app.route('/api/config', methods=['GET', 'POST'])
def manage_config():
    """
    Manages the application configuration.
    GET: Returns the current configuration.
    POST: Updates the configuration, saves it to app_config.py, and applies live changes.
    """
    # This must be at the top of the function because these variables are read in the GET case
    # and assigned in the POST case.
    global TEMP_HYSTERESIS, GLYCOL_TEMP_HYSTERESIS, GLYCOL_TARGET_OFFSET, READ_INTERVAL_SECONDS, control_mode
    if request.method == 'GET':
        config_data = {
            "NUM_FERMENTERS": NUM_FERMENTERS,
            "CHILLER_RELAY_PIN": CHILLER_RELAY_PIN,
            "PUMP_RELAY_PIN": PUMP_RELAY_PIN,
            "SOLENOID_PINS": SOLENOID_PINS,
            "HEATER_PINS": HEATER_PINS,
            "GLYCOL_SENSOR_ID": GLYCOL_SENSOR_ID,
            "PT100_SENSORS": PT100_SENSORS,
            "TEMP_HYSTERESIS": TEMP_HYSTERESIS,
            "DEFAULT_TARGET_GLYCOL_TEMP": DEFAULT_TARGET_GLYCOL_TEMP,
            "GLYCOL_TEMP_HYSTERESIS": GLYCOL_TEMP_HYSTERESIS,
            "GLYCOL_TARGET_OFFSET": GLYCOL_TARGET_OFFSET,
            "READ_INTERVAL_SECONDS": READ_INTERVAL_SECONDS,
            "MIN_GLYCOL_TEMP": MIN_GLYCOL_TEMP,
            "CONTROL_MODE": control_mode,
            "PID_PARAMS": PID_PARAMS,
            "FERMENTER_CONFIG": FERMENTER_CONFIG
        }
        return jsonify(config_data)

    if request.method == 'POST':
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data received"}), 400

        try:
            # --- Validate and coerce data types ---
            num_fermenters = int(data['NUM_FERMENTERS'])
            chiller_pin = int(data['CHILLER_RELAY_PIN'])
            pump_pin = int(data['PUMP_RELAY_PIN'])
            solenoid_pins_list = [int(p.strip()) for p in data['SOLENOID_PINS'].split(',') if p.strip()]
            heater_pins_list = [int(p.strip()) for p in data['HEATER_PINS'].split(',') if p.strip()]
            glycol_id = str(data['GLYCOL_SENSOR_ID']).strip()
            temp_hyst = float(data['TEMP_HYSTERESIS'])
            glycol_hyst = float(data['GLYCOL_TEMP_HYSTERESIS'])
            glycol_offset = float(data['GLYCOL_TARGET_OFFSET'])
            read_interval = int(data['READ_INTERVAL_SECONDS'])
            min_glycol = float(data['MIN_GLYCOL_TEMP'])
            default_glycol_temp = float(data['DEFAULT_TARGET_GLYCOL_TEMP'])
            
            # Control mode and PID parameters
            new_control_mode = data.get('CONTROL_MODE', 'bangbang')
            if new_control_mode not in ['bangbang', 'pid']:
                new_control_mode = 'bangbang'
            
            # PID parameters (optional, use defaults if not provided)
            pid_kp = float(data.get('PID_KP', PID_PARAMS.get('Kp', 20.0)))
            pid_ki = float(data.get('PID_KI', PID_PARAMS.get('Ki', 0.5)))
            pid_kd = float(data.get('PID_KD', PID_PARAMS.get('Kd', 5.0)))
            pid_duty_cycle = int(data.get('PID_DUTY_CYCLE', PID_PARAMS.get('duty_cycle_seconds', 60)))

            if not (len(solenoid_pins_list) == num_fermenters and len(heater_pins_list) == num_fermenters):
                return jsonify({"status": "error", "message": "Number of fermenters must match the length of pin lists (solenoids & heaters)."}), 400

            # Update live parameters
            TEMP_HYSTERESIS, GLYCOL_TEMP_HYSTERESIS, GLYCOL_TARGET_OFFSET, READ_INTERVAL_SECONDS = temp_hyst, glycol_hyst, glycol_offset, read_interval
            control_mode = new_control_mode
            PID_PARAMS['Kp'] = pid_kp
            PID_PARAMS['Ki'] = pid_ki
            PID_PARAMS['Kd'] = pid_kd
            PID_PARAMS['duty_cycle_seconds'] = pid_duty_cycle
            save_settings()  # Save runtime settings including control_mode
            print(f"Live configuration parameters updated. Control mode: {control_mode}")

            # --- Rewrite app_config.py ---
            pt100_config_repr = repr(PT100_SENSORS)  # Preserve existing PT100 config with Python syntax
            config_content = f"""# app_config.py
# This file is managed by the application's web UI.
# Manual edits may be overwritten.

# Define the number of fermenters you have
NUM_FERMENTERS = {num_fermenters}

# GPIO Pin assignments (BCM numbering)
CHILLER_RELAY_PIN = {chiller_pin}
PUMP_RELAY_PIN = {pump_pin}
SOLENOID_PINS = {solenoid_pins_list}
HEATER_PINS = {heater_pins_list}

# DS18B20 sensor ID for glycol bath
GLYCOL_SENSOR_ID = '{glycol_id}'

# PT100 Sensor Configuration (edit manually if needed)
PT100_SENSORS = {pt100_config_repr}

# Pins that need to be initialized as OUTPUT HIGH for SPI Chip Selects
SPI_CS_PINS = {SPI_CS_PINS}

# Control parameters
TEMP_HYSTERESIS = {temp_hyst}
DEFAULT_TARGET_GLYCOL_TEMP = {default_glycol_temp}
GLYCOL_TEMP_HYSTERESIS = {glycol_hyst}
GLYCOL_TARGET_OFFSET = {glycol_offset}
READ_INTERVAL_SECONDS = {read_interval}
MIN_GLYCOL_TEMP = {min_glycol}

CONTROL_MODE = "{new_control_mode}"

# PID Controller Parameters
PID_PARAMS = {{'Kp': {pid_kp}, 'Ki': {pid_ki}, 'Kd': {pid_kd}, 'duty_cycle_seconds': {pid_duty_cycle}}}

# Fermenter-specific configuration
FERMENTER_CONFIG = {repr(FERMENTER_CONFIG)}

# Glycol chiller protection (prevents short cycling)
CHILLER_MIN_ON_TIME = {CHILLER_MIN_ON_TIME}   # Minimum on time in seconds
CHILLER_MIN_OFF_TIME = {CHILLER_MIN_OFF_TIME}  # Minimum off time in seconds

# Dynamic glycol setpoint mode: True = adjust based on demand, False = fixed offset
DYNAMIC_GLYCOL_SETPOINT = {DYNAMIC_GLYCOL_SETPOINT}

SETTINGS_FILE = '{SETTINGS_FILE}'
"""
            with open('app_config.py', 'w') as f:
                f.write(config_content)
            print(f"Configuration saved to app_config.py")
            return jsonify({"status": "success", "message": "Configuration saved. Some changes may require a restart."})
        except (ValueError, TypeError) as e:
            return jsonify({"status": "error", "message": f"Invalid data format: {e}"}), 400
        except KeyError as e:
            return jsonify({"status": "error", "message": f"Missing required field: {e}"}), 400
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

# --- Main Execution ---
if __name__ == '__main__':
    print("--- RUNNING IN HARDWARE MODE ---")
    
    init_db() # Initialize the database
    load_settings() # Load settings at startup
    setup_gpio()

    # Start the control loop in a separate daemon thread
    control_thread = threading.Thread(target=control_loop, daemon=True)
    control_thread.start()

    # Start a background thread to clean up old logs periodically
    def periodic_cleanup():
        while True:
            # Wait 24 hours before running the cleanup
            time.sleep(24 * 60 * 60)
            print("Running periodic database cleanup...")
            cleanup_old_logs()

    cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
    cleanup_thread.start()
    print("Database cleanup scheduler started. Will run every 24 hours.")

    # Run the Flask web server
    # Use WEB_PORT from config, default to 80 if not defined
    port_to_use = globals().get('WEB_PORT', 80)
    app.run(host='0.0.0.0', port=port_to_use, debug=False)
