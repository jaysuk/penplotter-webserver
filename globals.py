import threading
from collections import deque

LOG_LINES = 300

printing = False
paused = False
wait_reason = None       # why the plot is held back: None (the pause button) or 'pen_change'
wait_pen = None          # the pen to load for a 'pen_change'
stop_requested = False   # the stop button was pressed for the current plot
plot_finished = False    # the data has been sent (or the plot stopped); only power-off waits are left
current_file = 'None'
start_stamp = 0
queue_active = False     # the queue runner is going through the queued plots
queue_hold = False       # Stop was pressed: the queue does not start the next plot
queue_message = ''       # why the queue last stopped

# What a browser needs to rebuild the plot view after a refresh, or when it connects while a
# plot is running (started from another client). Filled by record_event().
state_lock = threading.Lock()
plot_log = deque(maxlen=LOG_LINES)
plot_progress = 0
plot_bytes = ''
plot_buffer_size = None
plot_eta = None          # {'remaining': seconds, 'total': seconds}, when the plot could be analysed
drawn_seconds = None     # time spent sending the last plot, without pauses (for the ETA correction)
sent_offset = 0          # bytes of the file written to the plotter so far
cursor_ok = False        # the bytes sent are offsets in the file in the list (no chosen pens, not resumed)
buffer_used = 0          # of those, the bytes the plotter is known to still hold (buffer flow control only)
timelapse_id = None      # the timelapse being recorded for this plot, if any


def initialize():
    global printing, paused, wait_reason, wait_pen, stop_requested, plot_finished, current_file, start_stamp
    global queue_active, queue_hold, queue_message
    printing = False
    paused = False
    wait_reason = None
    wait_pen = None
    stop_requested = False
    plot_finished = False
    current_file = 'None'
    start_stamp = 0
    queue_active = False
    queue_hold = False
    queue_message = ''
    reset_plot_state()


def reset_plot_state():
    """Forget the previous plot's log and progress (called when a new plot starts)."""
    global plot_progress, plot_bytes, plot_buffer_size, plot_eta, drawn_seconds, sent_offset, buffer_used, cursor_ok
    global timelapse_id
    with state_lock:
        plot_log.clear()
        plot_progress = 0
        plot_bytes = ''
        plot_buffer_size = None
        plot_eta = None
        drawn_seconds = None
        sent_offset = 0
        buffer_used = 0
        cursor_ok = False
        timelapse_id = None


def clear_wait():
    """The plot is no longer held back."""
    global paused, wait_reason, wait_pen
    paused = False
    wait_reason = None
    wait_pen = None


def record_event(name, payload):
    """Remember the plot related realtime events (payloads are {'data': ...})."""
    global plot_progress, plot_bytes, plot_buffer_size, plot_eta
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
        elif name == 'eta':
            plot_eta = data


def plot_state(running, file):
    """Snapshot sent to the UI. `running` and `file` come from the plot lock in main.py."""
    with state_lock:
        return {
            'running': running,
            'paused': running and paused,
            'wait_reason': wait_reason if running and paused else None,
            'pen': wait_pen if running and paused else None,
            'eta': plot_eta if running else None,
            'file': file if running else None,
            'queue_active': queue_active,
            'cursor_ok': cursor_ok if running else False,
            'timelapse': timelapse_id if running else None,
            'progress': plot_progress,
            'bytes_written': plot_bytes,
            'buffer_size': plot_buffer_size if running else None,
        }


def plot_log_lines():
    with state_lock:
        return list(plot_log)
