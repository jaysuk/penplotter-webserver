"""A small persistent log of plots (SQLite, kept next to config.ini).

Nothing here may break plotting: every function catches database errors, prints them and carries on.
"""
import contextlib
import json
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


# Columns added after the first release: (name, type). A database made by an older version gets them
# when the server starts.
NEW_COLUMNS = (('estimate_s', 'REAL'), ('drawn_s', 'REAL'),
               ('options', 'TEXT'),             # the form the plot was started with (JSON), for plotting again
               ('file_size', 'INTEGER'),        # size of the file when it was plotted, to spot a changed file
               ('resume_offset', 'INTEGER'))    # where to carry on after a stop or failure


def _add_missing_columns(conn):
    have = {row['name'] for row in conn.execute('PRAGMA table_info(jobs)')}
    for name, kind in NEW_COLUMNS:
        if name not in have:
            conn.execute('ALTER TABLE jobs ADD COLUMN {} {}'.format(name, kind))


@contextlib.contextmanager
def database():
    """A locked connection, for modules that keep their own tables in history.db."""
    with _lock, _connect() as conn:
        yield conn


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
            _add_missing_columns(conn)
            conn.execute("UPDATE jobs SET status = 'interrupted', finished_at = ?, "
                         "error = 'The web plotter stopped while this plot was running' "
                         "WHERE status = ?", (time.time(), RUNNING))
    except sqlite3.Error as e:
        print('Plot history unavailable:', repr(e))


def start(file, port, baudrate, flow_control, options=None, file_size=None):
    """Record a plot that is starting. Returns its id, or None if it could not be recorded.

    `options` is the dict of form values the plot was started with."""
    try:
        with _lock, _connect() as conn:
            cursor = conn.execute(
                'INSERT INTO jobs (file, port, baudrate, flow_control, started_at, status, options, file_size) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (file, port, int(baudrate), flow_control, time.time(), RUNNING,
                 json.dumps(options) if options else None, file_size))
            job = cursor.lastrowid
            # Keep the table small: drop the oldest finished plots
            conn.execute('DELETE FROM jobs WHERE status != ? AND id NOT IN '
                         '(SELECT id FROM jobs ORDER BY id DESC LIMIT ?)', (RUNNING, MAX_ROWS))
            return job
    except (sqlite3.Error, ValueError) as e:
        print('Could not record the plot in the history:', repr(e))
        return None


def set_estimate(job, seconds):
    """Remember the uncorrected time estimate of a running plot."""
    if job is None or not seconds:
        return
    try:
        with _lock, _connect() as conn:
            conn.execute('UPDATE jobs SET estimate_s = ? WHERE id = ?', (float(seconds), job))
    except (sqlite3.Error, ValueError) as e:
        print('Could not record the plot estimate:', repr(e))


def finish(job, status, progress=0, error=None, drawn_s=None, resume_offset=None):
    """`drawn_s` is the time spent drawing (without pauses), for the estimate's correction.
    `resume_offset` is where a stopped or failed plot can carry on (a byte offset in the file)."""
    if job is None:
        return
    try:
        with _lock, _connect() as conn:
            conn.execute('UPDATE jobs SET status = ?, finished_at = ?, progress = ?, error = ?, drawn_s = ?, '
                         'resume_offset = ? WHERE id = ?',
                         (status, time.time(), int(progress or 0), error, drawn_s, resume_offset, job))
    except (sqlite3.Error, ValueError) as e:
        print('Could not update the plot history:', repr(e))


def _row(row):
    job = dict(row)
    try:
        job['options'] = json.loads(job['options']) if job.get('options') else None
    except ValueError:
        job['options'] = None
    return job


def recent(limit=50):
    """Newest first."""
    try:
        with _lock, _connect() as conn:
            rows = conn.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT ?', (int(limit),)).fetchall()
        return [_row(row) for row in rows]
    except sqlite3.Error as e:
        print('Plot history unavailable:', repr(e))
        return []


def get(job):
    """One plot by id, or None."""
    try:
        with _lock, _connect() as conn:
            row = conn.execute('SELECT * FROM jobs WHERE id = ?', (int(job),)).fetchone()
        return _row(row) if row else None
    except (sqlite3.Error, ValueError, TypeError) as e:
        print('Plot history unavailable:', repr(e))
        return None


def correction(limit=10):
    """How much longer (or shorter) plots really take than the model estimates: the median of
    drawn / estimated over the latest completed plots, kept within sensible bounds."""
    try:
        with _lock, _connect() as conn:
            rows = conn.execute(
                'SELECT estimate_s, drawn_s FROM jobs WHERE status = ? AND estimate_s > 0 AND drawn_s > 0 '
                'ORDER BY id DESC LIMIT ?', ('completed', int(limit))).fetchall()
    except sqlite3.Error as e:
        print('Plot history unavailable:', repr(e))
        return 1.0
    ratios = sorted(row['drawn_s'] / row['estimate_s'] for row in rows)
    if not ratios:
        return 1.0
    middle = len(ratios) // 2
    median = ratios[middle] if len(ratios) % 2 else (ratios[middle - 1] + ratios[middle]) / 2
    return min(max(median, 0.3), 5.0)


def clear():
    """Delete the finished plots (one that is running stays until it ends)."""
    try:
        with _lock, _connect() as conn:
            conn.execute('DELETE FROM jobs WHERE status != ?', (RUNNING,))
    except sqlite3.Error as e:
        print('Could not clear the plot history:', repr(e))
