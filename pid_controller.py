# pid_controller.py
# PID Controller and Duty Cycle Manager for Fermentation Temperature Control

import time

class PIDController:
    """
    A standard PID controller with anti-windup protection.
    
    Output range: -100 (full cooling) to +100 (full heating)
    """
    
    def __init__(self, Kp=20.0, Ki=0.5, Kd=5.0, min_output=-100, max_output=100):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.min_output = min_output
        self.max_output = max_output
        
        # Internal state
        self.integral = 0.0
        self.last_error = 0.0
        self.last_time = None
    
    def reset(self):
        """Reset the controller state."""
        self.integral = 0.0
        self.last_error = 0.0
        self.last_time = None
    
    def compute(self, setpoint, current_temp):
        """
        Compute the PID output.
        
        Args:
            setpoint: Target temperature
            current_temp: Current temperature reading
            
        Returns:
            Output value between min_output and max_output
            Negative = cooling needed, Positive = heating needed
        """
        current_time = time.time()
        
        # Calculate dt (time since last computation)
        if self.last_time is None:
            dt = 1.0  # Default to 1 second on first call
        else:
            dt = current_time - self.last_time
            if dt <= 0:
                dt = 1.0
        
        # Calculate error
        error = setpoint - current_temp
        
        # Proportional term
        P = self.Kp * error
        
        # Integral term with anti-windup
        self.integral += error * dt
        # Clamp integral to prevent windup
        max_integral = (self.max_output - self.min_output) / (2 * self.Ki) if self.Ki != 0 else 1000
        self.integral = max(-max_integral, min(max_integral, self.integral))
        I = self.Ki * self.integral
        
        # Derivative term (on error, not on measurement to avoid derivative kick)
        D = self.Kd * (error - self.last_error) / dt if dt > 0 else 0
        
        # Calculate output
        output = P + I + D
        
        # Clamp output
        output = max(self.min_output, min(self.max_output, output))
        
        # Store for next iteration
        self.last_error = error
        self.last_time = current_time
        
        return output
    
    def get_components(self):
        """Get the last P, I, D components for debugging."""
        return {
            "P": self.Kp * self.last_error,
            "I": self.Ki * self.integral,
            "D": 0  # Would need to store this
        }


class DutyCycleManager:
    """
    Manages time-proportioned output for on/off relays.
    
    Converts a duty cycle percentage (0-100%) into on/off states
    over a configurable cycle period.
    """
    
    def __init__(self, cycle_seconds=60):
        self.cycle_seconds = cycle_seconds
        self.cycle_start = time.time()
    
    def should_be_on(self, duty_percent):
        """
        Determine if the relay should be ON based on duty cycle.
        
        Args:
            duty_percent: 0-100% duty cycle
            
        Returns:
            True if relay should be ON, False if OFF
        """
        if duty_percent <= 0:
            return False
        if duty_percent >= 100:
            return True
        
        # Calculate position in current cycle
        elapsed = (time.time() - self.cycle_start) % self.cycle_seconds
        on_time = self.cycle_seconds * (duty_percent / 100.0)
        
        return elapsed < on_time
    
    def reset(self):
        """Reset the cycle start time."""
        self.cycle_start = time.time()


class ChillerController:
    """
    Controls the glycol chiller with minimum on/off times to prevent short cycling.
    """
    
    def __init__(self, min_on_time=300, min_off_time=180):
        self.min_on_time = min_on_time    # 5 minutes default
        self.min_off_time = min_off_time  # 3 minutes default
        self.is_on = False
        self.last_state_change = 0
    
    def should_turn_on(self, current_temp, target_temp, hysteresis=1.0):
        """
        Determine if chiller should be on, respecting minimum cycle times.
        
        Returns:
            True if chiller should be ON
        """
        current_time = time.time()
        time_in_state = current_time - self.last_state_change
        
        # Want to turn on? (glycol too warm)
        wants_on = current_temp > target_temp + hysteresis
        # Want to turn off? (glycol cold enough)
        wants_off = current_temp < target_temp - hysteresis
        
        if self.is_on:
            # Currently on - can we turn off?
            if wants_off and time_in_state >= self.min_on_time:
                self.is_on = False
                self.last_state_change = current_time
        else:
            # Currently off - can we turn on?
            if wants_on and time_in_state >= self.min_off_time:
                self.is_on = True
                self.last_state_change = current_time
        
        return self.is_on


def pid_output_to_duty_cycles(pid_output):
    """
    Convert PID output (-100 to +100) to heating and cooling duty cycles.
    
    Args:
        pid_output: -100 (full cooling) to +100 (full heating)
        
    Returns:
        (heating_duty, cooling_duty) - each 0-100%
    """
    if pid_output > 0:
        # Heating needed
        return (pid_output, 0)
    elif pid_output < 0:
        # Cooling needed
        return (0, -pid_output)
    else:
        return (0, 0)


def calculate_dynamic_glycol_target(cooling_duties, fermenter_targets, min_glycol_temp=-5, base_offset=5):
    """
    Calculate optimal glycol setpoint based on cooling demand.
    
    Args:
        cooling_duties: List of cooling duty cycles (0-100%) per fermenter
        fermenter_targets: List of target temperatures per fermenter
        min_glycol_temp: Minimum allowed glycol temperature
        base_offset: Default offset below lowest target
        
    Returns:
        Optimal glycol target temperature
    """

    
    max_cooling_demand = max(cooling_duties)
    
    # Only consider active fermenters (those with cooling demand or near their target)
    active_targets = [t for i, t in enumerate(fermenter_targets) if cooling_duties[i] > 0 or True]
    if not active_targets:
        return None  # No active cooling, glycol can be warm
    
    lowest_target = min(active_targets)
    
    if max_cooling_demand == 0:
        # ABSOLUTELY NO cooling needed - Set target high to ensure chiller stays off
        target = lowest_target + 5
    elif max_cooling_demand < 30:
        # Light cooling - glycol can be warmer (more efficient)
        target = lowest_target - 2
    elif max_cooling_demand < 70:
        # Moderate cooling
        target = lowest_target - 4
    else:
        # Heavy cooling demand - need cold glycol
        target = lowest_target - base_offset
    
    # Enforce minimum
    return max(min_glycol_temp, target)
