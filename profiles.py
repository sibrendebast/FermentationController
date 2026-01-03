# profiles.py
# Fermentation Profile Management Module

import json
import uuid
from datetime import datetime, timezone

PROFILES_FILE = 'fermentation_profiles.json'

# Default fermentation profiles
DEFAULT_PROFILES = [
    {
        "id": "ale-standard",
        "name": "Standard Ale",
        "description": "Classic ale fermentation with free rise and cold crash",
        "steps": [
            {"name": "Pitch", "target_temp": 18.0, "duration_hours": 0, "ramp_hours": 0},
            {"name": "Primary", "target_temp": 18.0, "duration_hours": 72, "ramp_hours": 0},
            {"name": "Free Rise", "target_temp": 22.0, "duration_hours": 48, "ramp_hours": 24},
            {"name": "Cold Crash", "target_temp": 2.0, "duration_hours": 48, "ramp_hours": 12}
        ]
    },
    {
        "id": "lager-standard",
        "name": "Standard Lager",
        "description": "Traditional lager fermentation with D-rest and extended cold conditioning",
        "steps": [
            {"name": "Pitch", "target_temp": 10.0, "duration_hours": 0, "ramp_hours": 0},
            {"name": "Primary", "target_temp": 10.0, "duration_hours": 168, "ramp_hours": 0},
            {"name": "D-Rest", "target_temp": 18.0, "duration_hours": 48, "ramp_hours": 24},
            {"name": "Cold Crash", "target_temp": 0.0, "duration_hours": 168, "ramp_hours": 24}
        ]
    },
    {
        "id": "saison",
        "name": "Saison",
        "description": "High-temperature saison fermentation for phenolic and fruity character",
        "steps": [
            {"name": "Pitch", "target_temp": 20.0, "duration_hours": 0, "ramp_hours": 0},
            {"name": "Primary", "target_temp": 25.0, "duration_hours": 72, "ramp_hours": 24},
            {"name": "Free Rise", "target_temp": 32.0, "duration_hours": 96, "ramp_hours": 48},
            {"name": "Cold Crash", "target_temp": 4.0, "duration_hours": 48, "ramp_hours": 24}
        ]
    },
    {
        "id": "kveik",
        "name": "Kveik",
        "description": "Hot and fast Kveik fermentation",
        "steps": [
            {"name": "Pitch", "target_temp": 30.0, "duration_hours": 0, "ramp_hours": 0},
            {"name": "Primary", "target_temp": 35.0, "duration_hours": 48, "ramp_hours": 6},
            {"name": "Cold Crash", "target_temp": 2.0, "duration_hours": 24, "ramp_hours": 12}
        ]
    },
    {
        "id": "sour-kettle",
        "name": "Kettle Sour",
        "description": "Lactobacillus acidification followed by clean ale fermentation",
        "steps": [
            {"name": "Acidification", "target_temp": 35.0, "duration_hours": 48, "ramp_hours": 0},
            {"name": "Pitch", "target_temp": 18.0, "duration_hours": 0, "ramp_hours": 2},
            {"name": "Primary", "target_temp": 20.0, "duration_hours": 72, "ramp_hours": 12},
            {"name": "Cold Crash", "target_temp": 2.0, "duration_hours": 48, "ramp_hours": 12}
        ]
    }
]


def load_profiles():
    """Load profiles from file, or return defaults if file doesn't exist."""
    try:
        with open(PROFILES_FILE, 'r') as f:
            data = json.load(f)
            return data.get('profiles', DEFAULT_PROFILES)
    except FileNotFoundError:
        # Create file with defaults
        save_profiles(DEFAULT_PROFILES)
        return DEFAULT_PROFILES
    except json.JSONDecodeError:
        print(f"Error reading {PROFILES_FILE}, using defaults")
        return DEFAULT_PROFILES


def save_profiles(profiles):
    """Save profiles to file."""
    try:
        with open(PROFILES_FILE, 'w') as f:
            json.dump({'profiles': profiles}, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving profiles: {e}")
        return False


def get_profile_by_id(profile_id):
    """Get a specific profile by its ID."""
    profiles = load_profiles()
    for profile in profiles:
        if profile['id'] == profile_id:
            return profile
    return None


def create_profile(name, description, steps):
    """Create a new profile."""
    profiles = load_profiles()
    new_profile = {
        "id": str(uuid.uuid4()),
        "name": name,
        "description": description,
        "steps": steps
    }
    profiles.append(new_profile)
    save_profiles(profiles)
    return new_profile


def update_profile(profile_id, name, description, steps):
    """Update an existing profile."""
    profiles = load_profiles()
    for i, profile in enumerate(profiles):
        if profile['id'] == profile_id:
            profiles[i] = {
                "id": profile_id,
                "name": name,
                "description": description,
                "steps": steps
            }
            save_profiles(profiles)
            return profiles[i]
    return None


def delete_profile(profile_id):
    """Delete a profile by ID."""
    profiles = load_profiles()
    profiles = [p for p in profiles if p['id'] != profile_id]
    save_profiles(profiles)
    return True


def calculate_current_step(profile, start_time):
    """
    Calculate which step we should be on based on elapsed time.
    Returns (step_index, target_temperature, time_remaining_in_step_hours)
    """
    if not profile or not start_time:
        return None, None, None
    
    try:
        if isinstance(start_time, str):
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        else:
            start_dt = start_time
        
        elapsed_hours = (datetime.now(timezone.utc) - start_dt).total_seconds() / 3600
    except:
        return None, None, None
    
    steps = profile.get('steps', [])
    if not steps:
        return None, None, None
    
    cumulative_hours = 0
    
    for i, step in enumerate(steps):
        step_duration = step.get('duration_hours', 0)
        ramp_hours = step.get('ramp_hours', 0)
        total_step_time = step_duration + ramp_hours
        
        # If this is a "hold" step (duration=0), it's instantaneous (just a starting point)
        if step_duration == 0 and ramp_hours == 0:
            if elapsed_hours <= cumulative_hours:
                return i, step['target_temp'], 0
            continue
        
        step_end_hours = cumulative_hours + total_step_time
        
        if elapsed_hours < step_end_hours:
            # We're in this step
            time_in_step = elapsed_hours - cumulative_hours
            time_remaining = step_end_hours - elapsed_hours
            
            # Calculate temperature during ramp
            if ramp_hours > 0 and time_in_step < ramp_hours:
                # We're in the ramp phase
                prev_temp = steps[i-1]['target_temp'] if i > 0 else step['target_temp']
                target_temp = step['target_temp']
                ramp_progress = time_in_step / ramp_hours
                current_target = prev_temp + (target_temp - prev_temp) * ramp_progress
                return i, round(current_target, 1), time_remaining
            else:
                # We're in the hold phase
                return i, step['target_temp'], time_remaining
        
        cumulative_hours = step_end_hours
    
    # Profile completed - stay at final step temperature
    if steps:
        return len(steps) - 1, steps[-1]['target_temp'], 0
    
    return None, None, None


def get_profile_total_duration(profile):
    """Get total duration of a profile in hours."""
    if not profile:
        return 0
    
    total = 0
    for step in profile.get('steps', []):
        total += step.get('duration_hours', 0) + step.get('ramp_hours', 0)
    return total
