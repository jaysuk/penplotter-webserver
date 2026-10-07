"""The queue of plots waiting to be run one after another (a table of history.db).

The module is called plot_queue because `queue` is a standard library module that Flask-SocketIO
imports. Like the history, nothing here may crash the server: database errors are printed and the
caller gets an empty result.
"""
import json
import sqlite3

import history

MAX_ITEMS = 50

WAITING = 'waiting'
RUNNING = 'running'


def init():
    """Create the table. After a restart nothing is running any more, so a plot that was running
    goes back to waiting (it was cut short, it was not done)."""
    try:
        with history.database() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS queue_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position INTEGER NOT NULL,
                file TEXT NOT NULL,
                options TEXT NOT NULL,
                pause_after INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'waiting')''')
            conn.execute('UPDATE queue_items SET status = ? WHERE status = ?', (WAITING, RUNNING))
    except sqlite3.Error as e:
        print('Plot queue unavailable:', repr(e))


def _item(row):
    item = dict(row)
    item['pause_after'] = bool(item['pause_after'])
    try:
        item['options'] = json.loads(item['options'])
    except ValueError:
        item['options'] = {}
    return item


def items():
    """The whole queue, in the order the plots run."""
    try:
        with history.database() as conn:
            rows = conn.execute('SELECT * FROM queue_items ORDER BY position, id').fetchall()
        return [_item(row) for row in rows]
    except sqlite3.Error as e:
        print('Plot queue unavailable:', repr(e))
        return []


def get(item_id):
    try:
        with history.database() as conn:
            row = conn.execute('SELECT * FROM queue_items WHERE id = ?', (int(item_id),)).fetchone()
        return _item(row) if row else None
    except (sqlite3.Error, ValueError, TypeError) as e:
        print('Plot queue unavailable:', repr(e))
        return None


def add(options, pause_after=False):
    """Add a plot (the dict of form values from main.plot_request) at the end. Returns its id, or
    None when the queue is full or cannot be written."""
    try:
        with history.database() as conn:
            count, last = conn.execute('SELECT COUNT(*), COALESCE(MAX(position), 0) FROM queue_items').fetchone()
            if count >= MAX_ITEMS:
                return None
            cursor = conn.execute(
                'INSERT INTO queue_items (position, file, options, pause_after) VALUES (?, ?, ?, ?)',
                (last + 1, options['file'], json.dumps(options), 1 if pause_after else 0))
            return cursor.lastrowid
    except (sqlite3.Error, KeyError) as e:
        print('Could not add to the plot queue:', repr(e))
        return None


def room():
    """How many more plots fit in the queue."""
    return max(MAX_ITEMS - len(items()), 0)


def reorder(ids):
    """Put the waiting plots in the order of `ids`, which must name every waiting plot exactly once
    (a plot that is running stays where it is). Returns False when it does not, or on a database error."""
    try:
        ids = [int(i) for i in ids]
    except (ValueError, TypeError):
        return False
    try:
        with history.database() as conn:
            rows = conn.execute('SELECT id, position FROM queue_items WHERE status = ? ORDER BY position, id',
                                (WAITING,)).fetchall()
            if sorted(ids) != sorted(row['id'] for row in rows):
                return False
            # The waiting plots keep the set of positions they had, handed out in the new order
            for item_id, position in zip(ids, sorted(row['position'] for row in rows)):
                conn.execute('UPDATE queue_items SET position = ? WHERE id = ?', (position, item_id))
    except sqlite3.Error as e:
        print('Could not reorder the plot queue:', repr(e))
        return False
    return True


def next_waiting():
    """The plot to run next, or None."""
    for item in items():
        if item['status'] == WAITING:
            return item
    return None


def count_waiting(exclude=None):
    return sum(1 for item in items() if item['status'] == WAITING and item['id'] != exclude)


def has_file(name):
    return any(item['file'] == name for item in items())


def _update(sql, args):
    try:
        with history.database() as conn:
            return conn.execute(sql, args).rowcount
    except (sqlite3.Error, ValueError, TypeError) as e:
        print('Could not update the plot queue:', repr(e))
        return 0


def set_status(item_id, status):
    _update('UPDATE queue_items SET status = ? WHERE id = ?', (status, item_id))


def set_pause_after(item_id, pause_after):
    return _update('UPDATE queue_items SET pause_after = ? WHERE id = ?', (1 if pause_after else 0, item_id))


def remove(item_id):
    """Remove a waiting plot. Returns False if there is none (the one that is running stays)."""
    return _update('DELETE FROM queue_items WHERE id = ? AND status = ?', (item_id, WAITING)) > 0


def done(item_id):
    """The plot that was running is finished: take it off the queue."""
    _update('DELETE FROM queue_items WHERE id = ?', (item_id,))


def clear():
    """Remove every waiting plot."""
    _update('DELETE FROM queue_items WHERE status = ?', (WAITING,))


def move(item_id, offset):
    """Move a waiting plot up (-1) or down (+1) among the waiting ones. Returns False when it
    cannot move that way."""
    waiting = [item for item in items() if item['status'] == WAITING]
    ids = [item['id'] for item in waiting]
    try:
        index = ids.index(int(item_id))
    except (ValueError, TypeError):
        return False
    other = index + (-1 if offset < 0 else 1)
    if not 0 <= other < len(waiting):
        return False
    a, b = waiting[index], waiting[other]
    try:
        with history.database() as conn:
            # Swap the two positions
            conn.execute('UPDATE queue_items SET position = ? WHERE id = ?', (b['position'], a['id']))
            conn.execute('UPDATE queue_items SET position = ? WHERE id = ?', (a['position'], b['id']))
    except sqlite3.Error as e:
        print('Could not move in the plot queue:', repr(e))
        return False
    return True
