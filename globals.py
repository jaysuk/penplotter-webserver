import threading
from collections import deque

LOG_LINES = 300

printing = False
paused = False
stop_requested = False   # the stop button was pressed for the current plot
plot_finished = False    # the data has been sent (or the plot stopped); only power-off waits are left
current_file = 'None'
start_stamp = 0

# What a browser needs to rebuild the plot view after a refresh, or when it connects while a
# plot is running (started from another client). Filled by record_event().
state_lock = threading.Lock()
plot_log = deque(maxlen=LOG_LINES)
plot_progress = 0
plot_bytes = ''
plot_buffer_size = None


def initialize():
    global printing, paused, stop_requested, plot_finished, current_file, start_stamp
    printing = False
    paused = False
    stop_requested = False
    plot_finished = False
    current_file = 'None'
    start_stamp = 0
    reset_plot_state()


def reset_plot_state():
    """Forget the previous plot's log and progress (called when a new plot starts)."""
    global plot_progress, plot_bytes, plot_buffer_size
    with state_lock:
        plot_log.clear()
        plot_progress = 0
        plot_bytes = ''
        plot_buffer_size = None


def record_event(name, payload):
    """Remember the plot related realtime events (payloads are {'data': ...})."""
    global plot_progress, plot_bytes, plot_buffer_size
    data = payload.get('data') if isinstance(payload, dict) else payload
    with state_lock:
        if name in ('status_log', 'error'):
            plot_log.append({'type': name, 'text': str(data)})
        elif name == 'bytes_written':
            plot_bytes = str(data)
        elif name == 'print_progress':
            plot_progress = data
        elif name == 'buffer_size':
            plot_buffer_size = data


def plot_state(running, file):
    """Snapshot sent to the UI. `running` and `file` come from the plot lock in main.py."""
    with state_lock:
        return {
            'running': running,
            'paused': running and paused,
            'file': file if running else None,
            'progress': plot_progress,
            'bytes_written': plot_bytes,
            'buffer_size': plot_buffer_size if running else None,
        }


def plot_log_lines():
    with state_lock:
        return list(plot_log)
