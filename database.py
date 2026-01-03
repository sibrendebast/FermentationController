import sqlite3
from datetime import datetime, timedelta, timezone

DATABASE_FILE = 'fermcontroller.db'
LOG_RETENTION_DAYS = 7*12 # How old data can be before it's purged

def get_db_connection():
    """
    Creates a database connection.
    `check_same_thread=False` is used because the app accesses the DB
    from the main thread (for API calls) and the control loop thread.
    For a simple application like this, it's a safe and easy solution.
    """
    conn = sqlite3.connect(DATABASE_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database and creates tables if they don't exist."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS temperature_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fermenter_index INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                temperature REAL NOT NULL
            )
        ''')
        conn.commit()
        print("Database initialized successfully.")
    except Exception as e:
        print(f"Error initializing database: {e}")
    finally:
        conn.close()

def log_temperature(fermenter_index, timestamp, temperature):
    """Logs a temperature reading to the database."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO temperature_log (fermenter_index, timestamp, temperature) VALUES (?, ?, ?)',
            (fermenter_index, timestamp.isoformat(), temperature)
        )
        conn.commit()
    except Exception as e:
        print(f"Error logging temperature to database: {e}")
    finally:
        conn.close()

def get_temperature_logs_for_fermenter(fermenter_index, start_date=None, end_date=None):
    """
    Retrieves temperature logs for a specific fermenter with automatic downsampling.
    :param fermenter_index: The index of the fermenter.
    :param start_date: ISO format start date string. If None, defaults to LOG_RETENTION_DAYS ago.
    :param end_date: ISO format end date string. If None, defaults to now.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Default end_date to now
        if end_date is None:
            end_timestamp = datetime.now(timezone.utc).isoformat()
        else:
            end_timestamp = end_date
        
        # Default start_date to LOG_RETENTION_DAYS ago
        if start_date is None:
            start_timestamp = (datetime.now(timezone.utc) - timedelta(days=LOG_RETENTION_DAYS)).isoformat()
        else:
            start_timestamp = start_date

        # Calculate the time range in days to determine downsampling interval
        try:
            start_dt = datetime.fromisoformat(start_timestamp.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end_timestamp.replace('Z', '+00:00'))
            range_days = (end_dt - start_dt).days
        except:
            range_days = LOG_RETENTION_DAYS  # Fallback

        # Determine downsampling interval based on time range
        # Target roughly 300-500 points per graph for optimal performance
        if range_days <= 1:
            interval_seconds = 300  # 5 minutes
        elif range_days <= 3:
            interval_seconds = 900  # 15 minutes
        elif range_days <= 7:
            interval_seconds = 1800  # 30 minutes
        elif range_days <= 30:
            interval_seconds = 3600  # 1 hour
        else:
            interval_seconds = 21600  # 6 hours

        # SQL query using SQLite's date/time functions for grouping
        query = '''
            SELECT 
                datetime((strftime('%s', timestamp) / ?) * ?, 'unixepoch') as aggr_timestamp,
                AVG(temperature) as avg_temp
            FROM temperature_log 
            WHERE fermenter_index = ? AND timestamp >= ? AND timestamp <= ?
            GROUP BY aggr_timestamp 
            ORDER BY aggr_timestamp ASC
        '''

        cursor.execute(query, (interval_seconds, interval_seconds, fermenter_index, start_timestamp, end_timestamp))
        rows = cursor.fetchall()
        
        return [{"timestamp": row["aggr_timestamp"] + "Z", "temperature": row["avg_temp"]} for row in rows]
    except Exception as e:
        print(f"Error retrieving temperature logs from database: {e}")
        return []
    finally:
        conn.close()

def cleanup_old_logs():
    """Removes log entries older than LOG_RETENTION_DAYS. Can be run periodically."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cutoff_timestamp = (datetime.now(timezone.utc) - timedelta(days=LOG_RETENTION_DAYS)).isoformat()
        cursor.execute('DELETE FROM temperature_log WHERE timestamp < ?', (cutoff_timestamp,))
        rows_deleted = cursor.rowcount
        conn.commit()
        if rows_deleted > 0:
            print(f"Database cleanup: Removed {rows_deleted} old log entries.")
    except Exception as e:
        print(f"Error during database cleanup: {e}")
    finally:
        conn.close()