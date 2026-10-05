"""Named sets of conversion options, kept in history.db next to the plot history.

Like the history, nothing here may break the app: database errors are printed and carry on.
Options are stored the way the convert form holds them (strings and checkboxes), so loading a
preset is just filling the form in, and what is stored passed the same validation as a conversion."""
import json
import re
import sqlite3
import time

import history

MAX_PRESETS = 50
NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,39}')


def init():
    try:
        with history.database() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS presets (
                name TEXT PRIMARY KEY,
                options TEXT NOT NULL,
                updated_at REAL NOT NULL)''')
    except sqlite3.Error as e:
        print('Conversion presets unavailable:', repr(e))


def valid_name(name):
    return isinstance(name, str) and NAME_RE.fullmatch(name) is not None


def all():
    """Every preset as [{'name', 'options'}], sorted by name."""
    try:
        with history.database() as conn:
            rows = conn.execute('SELECT name, options FROM presets ORDER BY name COLLATE NOCASE').fetchall()
        return [{'name': row['name'], 'options': json.loads(row['options'])} for row in rows]
    except (sqlite3.Error, ValueError) as e:
        print('Conversion presets unavailable:', repr(e))
        return []


def save(name, options):
    """Store (or replace) a preset. Returns None when saved, else the reason it was not."""
    if not valid_name(name):
        return 'A preset name is 1 to 40 letters, numbers, spaces or . _ ( ) + -'
    try:
        with history.database() as conn:
            exists = conn.execute('SELECT 1 FROM presets WHERE name = ?', (name,)).fetchone()
            if not exists and conn.execute('SELECT COUNT(*) FROM presets').fetchone()[0] >= MAX_PRESETS:
                return 'There are already {} presets. Delete one first.'.format(MAX_PRESETS)
            conn.execute('INSERT OR REPLACE INTO presets (name, options, updated_at) VALUES (?, ?, ?)',
                         (name, json.dumps(options), time.time()))
        return None
    except sqlite3.Error as e:
        print('Could not save the preset:', repr(e))
        return 'Could not save the preset'


def delete(name):
    """Returns True if there was such a preset."""
    try:
        with history.database() as conn:
            return conn.execute('DELETE FROM presets WHERE name = ?', (name,)).rowcount > 0
    except sqlite3.Error as e:
        print('Could not delete the preset:', repr(e))
        return False
