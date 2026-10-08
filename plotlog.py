"""A text log of what the sender and plot() did, kept on disk so that a plot that stopped by itself can be
explained afterwards (the log in the page is gone after a refresh or a restart, and a power cut loses it).

Flask-free. Nothing here may ever stop a plot: every function swallows its own errors. The file is
`userdata/plot.log` (the installer's update copies `userdata/`); it is turned over at MAX_BYTES, keeping one
older file, so it stays small on the Pi's SD card. Only events are written, never the plotter data."""
import os
import re
import threading
import time

PATH = os.path.join('userdata', 'plot.log')
OLD = PATH + '.1'
MAX_BYTES = 256 * 1024
MAX_LINE = 600
DEFAULT_LINES = 400
MAX_LINES = 5000

lock = threading.Lock()
CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]+')


def log(text):
    """Add one line, with the time. Never raises."""
    try:
        now = time.time()
        stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now)) + '.%03d' % int((now % 1) * 1000)
        line = '{} [{}] {}\n'.format(stamp, threading.current_thread().name, CONTROL_RE.sub(' | ', str(text))[:MAX_LINE])
        with lock:
            os.makedirs(os.path.dirname(PATH), exist_ok=True)
            try:
                if os.path.getsize(PATH) >= MAX_BYTES:
                    os.replace(PATH, OLD)
            except OSError:
                pass
            with open(PATH, 'a', encoding='utf-8', errors='replace') as f:
                f.write(line)
    except Exception:
        pass


def _read(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read().splitlines()
    except OSError:
        return []


def tail(lines=DEFAULT_LINES):
    """The last `lines` lines, oldest first (the older file is used when the current one is short)."""
    try:
        lines = max(1, min(int(lines), MAX_LINES))
    except (TypeError, ValueError):
        lines = DEFAULT_LINES
    with lock:
        current = _read(PATH)
        if len(current) < lines:
            current = _read(OLD)[-(lines - len(current)):] + current
    return '\n'.join(current[-lines:])


def everything():
    """Both files as one text, for a download."""
    with lock:
        return '\n'.join(_read(OLD) + _read(PATH))


def clear():
    """Delete the log. Returns True when nothing is left."""
    with lock:
        for path in (PATH, OLD):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError:
                return False
    return True
