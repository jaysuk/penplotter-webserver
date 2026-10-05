"""The serial connection dropping in the middle of a plot."""
import threading
import time

import pytest

from test_routes import wait_for

# A file with many short commands, so there is always a command boundary near any offset
FILE = b'IN;VS10;SP1;' + b''.join(b'PU%d,0;PD%d,10;' % (n, n) for n in range(1, 400)) + b'PU;SP0;'


class SIO:
    def __init__(self):
        self.events = []

    def emit(self, name, data=None):
        self.events.append((name, data))

    def names(self):
        return [name for name, _ in self.events]

    def errors(self):
        return [data['data'] for name, data in self.events if name == 'error']


@pytest.fixture
def run(app, uploads, monkeypatch):
    """Start sendToPlotter in a thread. Returns a function that starts it and an object holding
    the result and the events."""
    monkeypatch.setattr(app.send2serial, 'RECONNECT_DELAYS', (0.01,))
    threads = []

    def start(flow='CTS/RTS', content=FILE):
        (uploads / 'r.hpgl').write_bytes(content)
        start.sio = SIO()
        start.result = []
        thread = threading.Thread(
            target=lambda: start.result.append(app.send2serial.sendToPlotter(start.sio, 'uploads/r.hpgl', '/dev/x', 9600, flow)),
            daemon=True)
        threads.append(thread)
        thread.start()
        return thread

    yield start
    app.globals.printing = False         # a failing test must not leave the sender waiting
    app.globals.paused = False
    for thread in threads:
        thread.join(2)


def payload(port, buffered=True, queried=True):
    """The HPGL a port was sent (without the set-up and the buffer queries)."""
    writes = port.written
    if buffered:
        return b''.join(w for before, w in zip(writes, writes[1:]) if before == b'\033.B')
    return b''.join(writes[2 if queried else 1:])         # after the set-up (and the id query)


def resume_when_ready(app):
    assert wait_for(lambda: app.globals.wait_reason == 'reconnect')
    app.globals.paused = False          # what the Resume button does
    app.globals.wait_reason = None


@pytest.mark.parametrize('flow,buffered,break_at,unprocessed', [
    ('CTS/RTS', True, 12, 30),          # the plotter's buffer is known to be empty: just the last chunk is unsure
    ('XON/XOFF', False, 60, 1024),      # no feedback: a fixed rewind
])
def test_the_plot_carries_on_after_the_connection_comes_back(app, run, uploads, flow, buffered, break_at, unprocessed):
    app.serial.Serial.break_at = break_at
    thread = run(flow)
    assert wait_for(lambda: app.globals.wait_reason == 'disconnected')
    assert app.globals.paused is True
    assert 'wait_change' in run.sio.names()

    resume_when_ready(app)
    thread.join(5)
    assert run.result == [True] and not thread.is_alive()

    first, second = app.serial.Serial.instances
    assert first.closed and second.closed
    sent_before = len(payload(first, buffered))
    preamble, start = app.hpgl.resume_preamble(str(uploads / 'r.hpgl'), max(sent_before - unprocessed, 0))
    # The new port gets the plotter set up again, then the rest of the file from a command boundary at
    # or before where the first port got to (no gap), but not further back than the unprocessed bytes
    assert 0 < start <= sent_before and preamble.startswith(b'IN;VS10;SP1;PU;PA')
    assert payload(second, buffered, queried=False) == preamble + FILE[start:]
    assert 'Lost the connection' in run.sio.errors()[0]
    assert ('print_progress', {'data': 100}) in run.sio.events


def test_the_plot_waits_for_the_port_to_come_back(app, run):
    app.serial.Serial.break_at = 12
    app.serial.Serial.failures_after_break = 5
    thread = run()
    assert wait_for(lambda: app.globals.wait_reason == 'disconnected')
    assert wait_for(lambda: app.serial.Serial.open_failures == 0)
    resume_when_ready(app)
    thread.join(5)
    assert run.result == [True]
    assert len(app.serial.Serial.instances) == 2          # failed attempts never produced a port


def test_stop_while_waiting_for_the_port(app, run):
    app.serial.Serial.break_at = 12
    app.serial.Serial.failures_after_break = 1000
    thread = run()
    assert wait_for(lambda: app.globals.wait_reason == 'disconnected')
    app.globals.printing = False                           # the stop button
    thread.join(5)
    assert run.result == [True] and not thread.is_alive()
    assert 'end_of_print' in run.sio.names()
    assert ('print_progress', {'data': 100}) not in run.sio.events


def test_stop_after_the_port_came_back(app, run):
    app.serial.Serial.break_at = 12
    thread = run()
    assert wait_for(lambda: app.globals.wait_reason == 'reconnect')
    app.globals.printing = False
    thread.join(5)
    assert run.result == [True]
    assert all(port.closed for port in app.serial.Serial.instances)


def test_the_plot_is_given_up_when_the_port_never_comes_back(app, run, monkeypatch):
    monkeypatch.setattr(app.send2serial, 'RECONNECT_GIVE_UP', 0.05)
    app.serial.Serial.break_at = 12
    app.serial.Serial.failures_after_break = 1000
    thread = run()
    thread.join(5)
    assert run.result == [False]                           # a failed plot, not a finished one
    assert any('given up' in error for error in run.sio.errors())
    assert app.globals.paused is False and app.globals.wait_reason is None


def test_a_reconnected_plot_that_cannot_be_asked_for_its_buffer_fails(app, run):
    app.serial.Serial.break_at = 12
    thread = run()
    assert wait_for(lambda: app.globals.wait_reason == 'reconnect')
    app.serial.Serial.no_reply = True
    app.globals.paused = False
    thread.join(5)
    assert run.result == [False]
    assert any('does not answer' in error for error in run.sio.errors())


def test_pen_changes_are_not_asked_for_again(app, run, uploads):
    """A pen change that was already done stays done: the plot never goes back across it."""
    content = b'IN;SP1;' + b'PU0,0;PD10,10;' * 60 + b'SP2;' + b'PU0,0;PD20,20;' * 60 + b'PU;SP0;'
    (uploads / 'r.hpgl').write_bytes(content)
    analysis = app.hpgl.analyze(str(uploads / 'r.hpgl'))
    [(stop, pen)] = app.hpgl.pen_changes(analysis)

    sio = SIO()
    result = []
    thread = threading.Thread(daemon=True, target=lambda: result.append(app.send2serial.sendToPlotter(
        sio, 'uploads/r.hpgl', '/dev/x', 9600, 'XON/XOFF', analysis=analysis, pen_pause=True)))
    thread.start()
    assert wait_for(lambda: app.globals.wait_reason == 'pen_change')
    # The pen is changed and the plot goes on; the connection drops a few chunks later
    app.serial.Serial.instances[0].writes_left = 6
    app.globals.paused = False
    app.globals.wait_reason = None
    assert wait_for(lambda: app.globals.wait_reason == 'disconnected')
    resume_when_ready(app)
    thread.join(5)
    assert result == [True]
    sent = payload(app.serial.Serial.instances[-1], buffered=False, queried=False)
    # A rewind of 1 KB would reach back into pen 1's drawing, but it stops at the pen change
    assert sent.endswith(content[stop:]) and b'PD10,10' not in sent
    assert sio.names().count('pen_change') == 1
    assert app.globals.wait_reason is None


def test_resume_is_refused_while_the_plotter_is_unplugged(app, client):
    app.main.plot_lock.acquire()
    try:
        app.globals.paused = True
        app.globals.wait_reason = 'disconnected'
        assert client.post('/resume_plot').status_code == 409
        assert app.globals.paused is True                  # still held
        app.globals.wait_reason = 'reconnect'
        assert client.post('/resume_plot').status_code == 200
        assert app.globals.paused is False
    finally:
        app.main.plot_lock.release()
        app.globals.initialize()


def test_every_page_is_told_about_the_wait(app, client, uploads, monkeypatch):
    def fake_send(socketio, hpglfile, port, baud, flow, **kwargs):
        app.globals.printing = True
        app.globals.wait_reason = 'disconnected'
        app.globals.paused = True
        socketio.emit('wait_change', {'data': {'reason': 'disconnected'}})
        while app.globals.printing:
            time.sleep(0.005)
        return True

    monkeypatch.setattr(app.send2serial, 'sendToPlotter', fake_send)
    (uploads / 'a.hpgl').write_text('IN;')
    sc = app.main.socketio.test_client(app.main.app)
    sc.get_received()
    client.post('/start_plot', data=dict(file='a.hpgl', port='/dev/ttyAMA0', baudrate='9600', flowControl='CTS/RTS'))
    assert wait_for(lambda: app.globals.wait_reason == 'disconnected')
    states = []
    assert wait_for(lambda: states.extend(m['args'][0]['data'] for m in sc.get_received() if m['name'] == 'plot_state')
                    or any(s['wait_reason'] == 'disconnected' for s in states))
    client.post('/stop_plot')
    assert wait_for(lambda: not app.main.plot_lock.locked())
