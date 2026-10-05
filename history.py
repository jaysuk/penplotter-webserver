"""A small persistent log of plots (SQLite, kept next to config.ini).

Nothing here may break plotting: every function catches database errors, prints them and carries on.
"""
import contextlib
import sqlite3
import threading
import time

DB_PATH = 'history.db'
MAX_ROWS = 200

_lock = threading.Lock()

RUNNING = 'running'
FINISHED = ('completed', 'stopped', 'failed', 'interrupted')


@contextlib.contextmanager
def _connect():
    """A connection that commits and, unlike sqlite3's own context manager, also closes."""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init():
    """Create the table. A plot still marked running was cut short by a restart or power loss."""
    try:
        with _lock, _connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file TEXT NOT NULL,
                port TEXT,
                baudrate INTEGER,
                flow_control TEXT,
                started_at REAL NOT NULL,
                finished_at REAL,
                status TEXT NOT NULL,
                progress INTEGER DEFAULT 0,
                error TEXT)''')
            conn.execute("UPDATE jobs SET status = 'interrupted', finished_at = ?, "
                         "error = 'The web plotter stopped while this plot was running' "
                         "WHERE status = ?", (time.time(), RUNNING))
    except sqlite3.Error as e:
        print('Plot history unavailable:', repr(e))


def start(file, port, baudrate, flow_control):
    """Record a plot that is starting. Returns its id, or None if it could not be recorded."""
    try:
        with _lock, _connect() as conn:
            cursor = conn.execute(
                'INSERT INTO jobs (file, port, baudrate, flow_control, started_at, status) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (file, port, int(baudrate), flow_control, time.time(), RUNNING))
            job = cursor.lastrowid
            # Keep the table small: drop the oldest finished plots
            conn.execute('DELETE FROM jobs WHERE status != ? AND id NOT IN '
                         '(SELECT id FROM jobs ORDER BY id DESC LIMIT ?)', (RUNNING, MAX_ROWS))
            return job
    except (sqlite3.Error, ValueError) as e:
        print('Could not record the plot in the history:', repr(e))
        return None


def finish(job, status, progress=0, error=None):
    if job is None:
        return
    try:
        with _lock, _connect() as conn:
            conn.execute('UPDATE jobs SET status = ?, finished_at = ?, progress = ?, error = ? WHERE id = ?',
                         (status, time.time(), int(progress or 0), error, job))
    except (sqlite3.Error, ValueError) as e:
        print('Could not update the plot history:', repr(e))


def recent(limit=50):
    """Newest first."""
    try:
        with _lock, _connect() as conn:
            rows = conn.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT ?', (int(limit),)).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error as e:
        print('Plot history unavailable:', repr(e))
        return []


def clear():
    """Delete the finished plots (one that is running stays until it ends)."""
    try:
        with _lock, _connect() as conn:
            conn.execute('DELETE FROM jobs WHERE status != ?', (RUNNING,))
    except sqlite3.Error as e:
        print('Could not clear the plot history:', repr(e))
