"""Slow down guessing of the login (HTTP basic auth): after a few wrong passwords from one address
that address is refused for a while. Plain Python, no Flask; the app asks `blocked` before it checks
a password, and tells `failed` / `succeeded` afterwards."""
import threading
import time

MAX_FAILURES = 10       # wrong passwords within WINDOW seconds ...
WINDOW = 300
LOCKOUT = 300           # ... lock that address out for this long
MAX_TRACKED = 1000      # addresses remembered (a flood from many addresses cannot grow the table)

_lock = threading.Lock()
_failures = {}          # address -> [times of recent failures]
_locked_until = {}      # address -> time


def _forget_old(now):
    for address in [a for a, until in _locked_until.items() if until <= now]:
        del _locked_until[address]
        _failures.pop(address, None)
    for address in [a for a, times in _failures.items() if not times or times[-1] < now - WINDOW]:
        del _failures[address]


def blocked(address, now=None):
    """Seconds left before `address` may try again, or 0."""
    now = time.time() if now is None else now
    with _lock:
        until = _locked_until.get(address, 0)
    return max(int(until - now + 0.999), 0) if until > now else 0


def failed(address, now=None):
    """A wrong password from `address`. Returns True when that locks it out."""
    now = time.time() if now is None else now
    with _lock:
        _forget_old(now)
        if address not in _failures and len(_failures) >= MAX_TRACKED:
            return False
        times = [t for t in _failures.get(address, []) if t > now - WINDOW]
        times.append(now)
        _failures[address] = times
        if len(times) >= MAX_FAILURES:
            _locked_until[address] = now + LOCKOUT
            return True
    return False


def succeeded(address):
    with _lock:
        _failures.pop(address, None)


def reset():
    with _lock:
        _failures.clear()
        _locked_until.clear()
