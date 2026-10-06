import pytest

BIG_PLOT = b'PU0,0;PD100,100;' * 5000   # enough chunks for a plot to be stopped half way
SMALL_PLOT = b'IN;SP1;PU0,0;PD100,100;' * 40


class SIO:
    def __init__(self):
        self.events = []

    def emit(self, name, data=None):
        self.events.append((name, data))

    def names(self):
        return [name for name, _ in self.events]


@pytest.fixture
def plot(app, uploads):
    """Run sendToPlotter on a file and return (result, socketio events)."""
    def run(flow, content=SMALL_PLOT, name='t.hpgl'):
        (uploads / name).write_bytes(content)
        sio = SIO()
        result = app.send2serial.sendToPlotter(sio, 'uploads/' + name, '/dev/x', 9600, flow)
        return result, sio
    return run


@pytest.mark.parametrize('flow', ['CTS/RTS', 'Software', 'XON/XOFF', 'None', 'NONE', 'HP-IB'])
def test_every_flow_control_completes(app, plot, flow):
    result, sio = plot(flow)
    ports = app.serial.Serial.instances

    assert result is True
    assert len(ports) == 1 and ports[0].closed          # one port, always closed afterwards
    assert app.globals.printing is False
    assert ('print_progress', {'data': 100}) in sio.events
    assert 'end_of_print' in sio.names()


def test_port_settings_per_flow_control(app, plot):
    plot('XON/XOFF')
    assert app.serial.Serial.instances[-1].kwargs['xonxoff'] is True
    plot('HP-IB')
    hpib = app.serial.Serial.instances[-1].kwargs
    assert hpib['baudrate'] == 9600 and hpib['rtscts'] is True
    plot('CTS/RTS')
    # CTS is polled by hand: pyserial's own rtscts handling is buggy
    assert 'rtscts' not in app.serial.Serial.instances[-1].kwargs


@pytest.mark.parametrize('flow', ['XON/XOFF', 'None', 'HP-IB'])
def test_no_buffer_queries_without_buffer_flow_control(app, plot, flow):
    plot(flow)
    assert b'\033.B' not in app.serial.Serial.instances[0].written


def test_buffer_flow_control_feeds_the_chart(plot):
    _, sio = plot('CTS/RTS')
    assert 'buffer_size' in sio.names() and 'buffer_space' in sio.names()


def test_stop_aborts_the_plotter(app, plot):
    Serial = app.serial.Serial
    seen = {'chunks': 0}

    def stop_after_a_few_chunks(port, data):
        seen['chunks'] += 1
        if seen['chunks'] == 20:
            app.globals.printing = False   # what the stop button does

    Serial.on_data = stop_after_a_few_chunks
    result, sio = plot('CTS/RTS', BIG_PLOT)
    port = Serial.instances[0]

    assert result is True
    assert seen['chunks'] < 200                              # stopped long before the end
    assert b'\033.K' in port.written and b'PU;' in port.written   # buffered data is discarded
    assert port.closed and app.globals.printing is False
    assert 'end_of_print' in sio.names()
    assert not any(n == 'bytes_written' and 'EOP' in d['data'] for n, d in sio.events)


def test_errors_use_the_data_key_the_ui_reads(app, plot):
    app.serial.Serial.fail_open = True
    result, sio = plot('CTS/RTS')
    assert result is False
    assert sio.events[0][0] == 'error' and 'data' in sio.events[0][1]
    assert app.globals.printing is False


def test_missing_and_empty_files(app, plot):
    sio = SIO()
    assert app.send2serial.sendToPlotter(sio, 'uploads/missing.hpgl', '/dev/x', 9600, 'CTS/RTS') is False
    assert 'data' in sio.events[0][1] and app.globals.printing is False
    result, sio = plot('CTS/RTS', b'', 'empty.hpgl')
    assert result is False and 'empty' in sio.events[0][1]['data']


def test_unanswering_plotter_is_reported(plot, app):
    app.serial.Serial.no_reply = True
    result, sio = plot('CTS/RTS')
    assert result is False
    assert any(n == 'error' for n in sio.names())
    assert app.serial.Serial.instances[0].closed


def test_list_com_ports(app):
    assert app.send2serial.listComPorts() == {'name': 'ports', 'content': ['/dev/ttyAMA0', '/dev/ttyUSB1']}


def test_baud_detection(app):
    s2s, Serial = app.send2serial, app.serial.Serial
    assert s2s.getBaudRate('/dev/x') == 9600 or s2s.getBaudRate('/dev/x') is None

    Serial.no_reply = True
    assert s2s.getBaudRate('/dev/x') is None
    assert Serial.instances[-1].closed

    Serial.fail_open = True
    assert s2s.getBaudRate('/dev/x') is None


def test_baud_detection_survives_garbage(app, monkeypatch):
    # A wrong baud rate returns bytes that are not valid text
    monkeypatch.setattr(app.serial.Serial, 'read', lambda self, size=1: b'\xff\xfe\x80')
    app.send2serial.getBaudRate('/dev/x')   # must not raise UnicodeDecodeError
    assert app.serial.Serial.instances[-1].closed


def test_pause_holds_back_the_data_until_resumed(app, plot):
    import threading
    Serial = app.serial.Serial
    seen = {'chunks': 0, 'at_pause': None}

    def pause_after_a_few_chunks(port, data):
        seen['chunks'] += 1
        if seen['chunks'] == 20:
            app.globals.paused = True

            def resume():
                seen['at_pause'] = seen['chunks']   # nothing was written while paused
                app.globals.paused = False
            threading.Timer(0.4, resume).start()

    Serial.on_data = pause_after_a_few_chunks
    result, sio = plot('CTS/RTS', BIG_PLOT)

    assert result is True
    assert seen['at_pause'] <= 21 and seen['chunks'] > seen['at_pause']   # carried on after resuming
    assert ('print_progress', {'data': 100}) in sio.events


def test_stop_ends_a_paused_plot(app, plot):
    import threading
    Serial = app.serial.Serial
    seen = {'chunks': 0}

    def pause_then_stop(port, data):
        seen['chunks'] += 1
        if seen['chunks'] == 5:
            app.globals.paused = True
            # the stop button, pressed while the plot is held back
            threading.Timer(0.3, lambda: setattr(app.globals, 'printing', False)).start()

    Serial.on_data = pause_then_stop
    result, sio = plot('CTS/RTS', BIG_PLOT)

    assert result is True and seen['chunks'] == 5
    assert 'end_of_print' in sio.names() and Serial.instances[0].closed


def test_a_stop_that_came_first_is_not_undone(app, plot):
    app.globals.stop_requested = True            # the stop button, pressed just before the sender got going
    result, sio = plot('CTS/RTS', BIG_PLOT)
    port = app.serial.Serial.instances[0]
    assert result is True and app.globals.printing is False
    assert not any(w.startswith(b'PU0') for w in port.written)     # no plot data was sent
    assert 'end_of_print' in sio.names()


# ---- a plotter that holds the line (XOFF, switched off, out of paper) ---------------------------

def run_stalled(app, plot, flow, content=BIG_PLOT, name='stalled.hpgl', stop_after=0.3):
    """Plot while the port reports a full output queue, and press Stop after `stop_after` seconds."""
    import threading
    (plot.uploads / name).write_bytes(content)
    app.serial.Serial.backlog = 4000
    result = []
    thread = threading.Thread(daemon=True, target=lambda: result.append(
        app.send2serial.sendToPlotter(SIO(), 'uploads/' + name, '/dev/x', 9600, flow)))
    thread.start()
    threading.Timer(stop_after, lambda: setattr(app.globals, 'printing', False)).start()
    thread.join(5)
    return thread, result


@pytest.mark.parametrize('flow', ['XON/XOFF', 'None', 'CTS/RTS', 'HP-IB'])
def test_stop_works_while_the_plotter_holds_the_line(app, uploads, flow):
    plot = type('P', (), {'uploads': uploads})
    thread, result = run_stalled(app, plot, flow)
    port = app.serial.Serial.instances[0]

    assert not thread.is_alive() and result == [True]       # the sender did not stay in a write
    assert not any(w.startswith(b'PU0') for w in port.written)   # nothing was queued up on top of the backlog
    assert port.closed and port.flushed >= 1                # the unsent output was dropped before closing
    assert app.globals.printing is False


def test_a_stalled_plot_carries_on_when_the_plotter_takes_data_again(app, uploads):
    import threading
    (uploads / 'slow.hpgl').write_bytes(SMALL_PLOT)
    app.serial.Serial.backlog = 4000
    threading.Timer(0.3, lambda: setattr(app.serial.Serial, 'backlog', 0)).start()
    result = app.send2serial.sendToPlotter(SIO(), 'uploads/slow.hpgl', '/dev/x', 9600, 'XON/XOFF')
    assert result is True
    assert any(w.startswith(b'IN;SP1') or b'SP1' in w for w in app.serial.Serial.instances[0].written)


def test_a_stopped_cal_plot_lifts_the_pen_on_a_clean_queue(app, uploads):
    plot = type('P', (), {'uploads': uploads})
    thread, result = run_stalled(app, plot, 'XON/XOFF', content=b'R2;H;F1;C100,100;K;' * 400, name='s.cal')
    port = app.serial.Serial.instances[0]
    assert not thread.is_alive() and result == [True]
    assert port.written[-1] == b'H;' and port.flushed >= 1
