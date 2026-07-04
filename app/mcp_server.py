import os
import json
import datetime
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ElderlyCareMCPServer")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDS_FILE = os.path.join(BASE_DIR, "medication_store.json")
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule_store.json")

# Initialize default stores if they don't exist
if not os.path.exists(MEDS_FILE):
    default_meds = [
        {"name": "Lisinopril", "dosage": "10mg", "time": "08:00 AM", "purpose": "Blood Pressure"},
        {"name": "Metformin", "dosage": "500mg", "time": "07:00 PM", "purpose": "Diabetes"},
        {"name": "Multivitamin", "dosage": "1 tablet", "time": "08:00 AM", "purpose": "General Health"}
    ]
    with open(MEDS_FILE, "w") as f:
        json.dump(default_meds, f, indent=2)

if not os.path.exists(SCHEDULE_FILE):
    default_schedule = [
        {"time": "09:00 AM", "activity": "Morning Walk in the Garden"},
        {"time": "11:30 AM", "activity": "Call with daughter Susan"},
        {"time": "02:00 PM", "activity": "Doctor check-up at Health Center"}
    ]
    with open(SCHEDULE_FILE, "w") as f:
        json.dump(default_schedule, f, indent=2)

@mcp.tool()
def get_medications() -> str:
    """Retrieve the list of prescribed medications, their dosages, times, and purposes."""
    try:
        with open(MEDS_FILE, "r") as f:
            meds = json.load(f)
        return json.dumps(meds, indent=2)
    except Exception as e:
        return f"Error retrieving medications: {str(e)}"

@mcp.tool()
def log_medication_taken(med_name: str, time_taken: str) -> str:
    """Record that the user has taken their medication at a specific time.

    Args:
        med_name: The name of the medication taken.
        time_taken: The time the medication was taken (e.g. '08:15 AM').
    """
    try:
        log_file = os.path.join(BASE_DIR, "medication_logs.json")
        logs = []
        if os.path.exists(log_file):
            with open(log_file, "r") as f:
                logs = json.load(f)
        
        logs.append({
            "medication": med_name,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "time_taken": time_taken
        })
        
        with open(log_file, "w") as f:
            json.dump(logs, f, indent=2)
            
        return f"Successfully logged that {med_name} was taken at {time_taken}."
    except Exception as e:
        return f"Error logging medication: {str(e)}"

@mcp.tool()
def get_daily_schedule() -> str:
    """Retrieve today's schedule of activities and appointments."""
    try:
        with open(SCHEDULE_FILE, "r") as f:
            schedule = json.load(f)
        return json.dumps(schedule, indent=2)
    except Exception as e:
        return f"Error retrieving schedule: {str(e)}"

@mcp.tool()
def add_appointment(activity: str, time: str) -> str:
    """Add a new activity or doctor appointment to today's schedule.

    Args:
        activity: The description of the activity or appointment.
        time: The time of the appointment (e.g. '04:00 PM').
    """
    try:
        with open(SCHEDULE_FILE, "r") as f:
            schedule = json.load(f)
            
        schedule.append({
            "time": time,
            "activity": activity
        })
        
        # Sort schedule by time roughly
        schedule.sort(key=lambda x: x.get("time", ""))
        
        with open(SCHEDULE_FILE, "w") as f:
            json.dump(schedule, f, indent=2)
            
        return f"Successfully added '{activity}' at {time} to today's schedule."
    except Exception as e:
        return f"Error adding appointment: {str(e)}"

if __name__ == "__main__":
    mcp.run()
