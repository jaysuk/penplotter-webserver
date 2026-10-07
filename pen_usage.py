"""How far each pen has drawn since it was last replaced (a table of history.db).

The plotter has pen numbers, not pens, so the log is per pen number: after loading a new pen, reset
that number. Like the history, nothing here may stop a plot: database errors are printed and the
caller gets an empty result.
"""
import sqlite3
import time

import history


def init():
    try:
        with history.database() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS pen_usage (
                pen INTEGER PRIMARY KEY,
                mm REAL NOT NULL DEFAULT 0,
                plots INTEGER NOT NULL DEFAULT 0,
                since REAL NOT NULL)''')
    except sqlite3.Error as e:
        print('Pen log unavailable:', repr(e))


def add(drawn):
    """Add what a plot drew: {pen: millimetres of pen-down travel}. Pens that drew nothing are skipped."""
    drawn = {int(pen): float(mm) for pen, mm in (drawn or {}).items() if pen and mm and mm > 0}
    if not drawn:
        return
    now = time.time()
    try:
        with history.database() as conn:
            for pen, mm in drawn.items():
                conn.execute('INSERT OR IGNORE INTO pen_usage (pen, mm, plots, since) VALUES (?, 0, 0, ?)', (pen, now))
                conn.execute('UPDATE pen_usage SET mm = mm + ?, plots = plots + 1 WHERE pen = ?', (mm, pen))
    except (sqlite3.Error, ValueError) as e:
        print('Could not record the pen use:', repr(e))


def all_pens():
    """Every pen number that has drawn, as [{pen, mm, plots, since}]."""
    try:
        with history.database() as conn:
            rows = conn.execute('SELECT pen, mm, plots, since FROM pen_usage ORDER BY pen').fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error as e:
        print('Pen log unavailable:', repr(e))
        return []


def reset(pen):
    """A new pen was loaded: start that number's count again. Returns False if there was no such pen."""
    try:
        with history.database() as conn:
            return conn.execute('DELETE FROM pen_usage WHERE pen = ?', (int(pen),)).rowcount > 0
    except (sqlite3.Error, ValueError, TypeError) as e:
        print('Could not reset the pen use:', repr(e))
        return False
