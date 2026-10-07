"""Time left, pen change pauses and plotting selected pens, driven through the real sender with
the fake serial port."""
import os
import threading
import time

import pytest

from test_routes import PLOT, received, wait_for

LAYER = b'PU0,0;PD400,0,400,400,0,400,0,0;'
TWO_PENS = b'IN;SP1;' + LAYER * 30 + b'SP2;' + LAYER * 30 + b'SP0;'
THREE_PENS = b'IN;SP1;' + LAYER * 5 + b'SP2;' + LAYER * 5 + b'SP3;' + LAYER * 5 + b'SP0;'
# What the sender writes besides plot data
CONTROL = {b'\033.L', b'\033.B', b'IN;OI;', b'IN;\033.R', b'PU;',
           b'IN;\033.I80;;17:\033.N10;19:\033.@;0:'}   # XON/XOFF set-up


class SIO:
    def __init__(self):
        self.events = []

    def emit(self, name, data=None):
        self.events.append((name, data))

    def named(self, name):
        return [d['data'] for n, d in self.events if n == name]


def plot_data(port):
    """The HPGL the sender wrote to the (fake) port."""
    return b''.join(chunk for chunk in port.written if chunk not in CONTROL)


@pytest.fixture
def start(app, uploads):
    """Start the real sender in a thread. Returns (thread, sio, path)."""
    threads = []

    def run(content=TWO_PENS, flow='CTS/RTS', pen_pause=False, analyse=True, correction=1.0):
        (uploads / 'p.hpgl').write_bytes(content)
        path = 'uploads/p.hpgl'
        analysis = app.hpgl.analyze(path) if analyse else None
        sio = SIO()
        thread = threading.Thread(target=lambda: app.send2serial.sendToPlotter(
            sio, path, '/dev/x', 9600, flow, analysis=analysis, pen_pause=pen_pause, correction=correction),
            daemon=True)
        threads.append(thread)
        thread.start()
        return thread, sio, analysis
    yield run
    app.globals.printing = False      # a failed test must not leave a plot waiting for a pen
    for thread in threads:
        thread.join(5)
    leaked = [thread for thread in threads if thread.is_alive()]
    assert not leaked, 'a sender thread did not end: it would leak into the next test'


# ---- time left --------------------------------------------------------------------------------

def test_time_left_counts_down_to_zero(app, start):
    thread, sio, analysis = start()
    thread.join(10)
    etas = sio.named('eta')
    assert etas[0]['remaining'] == etas[0]['total'] == round(analysis['seconds'])
    assert etas[-1]['remaining'] == 0
    remaining = [e['remaining'] for e in etas]
    assert remaining == sorted(remaining, reverse=True)


def test_time_left_is_corrected(app, start):
    thread, sio, analysis = start(correction=2.0)
    thread.join(10)
    assert sio.named('eta')[0]['total'] == round(analysis['seconds'] * 2)


def test_no_time_left_without_an_analysis(app, start):
    thread, sio, _ = start(analyse=False)
    thread.join(10)
    assert sio.named('eta') == [] and ('end_of_print', {'data': 'True'}) in sio.events


def test_a_stale_analysis_is_ignored(app, uploads):
    (uploads / 'p.hpgl').write_bytes(TWO_PENS)
    analysis = app.hpgl.analyze('uploads/p.hpgl')
    (uploads / 'p.hpgl').write_bytes(TWO_PENS + LAYER)       # changed after it was analysed
    sio = SIO()
    app.send2serial.sendToPlotter(sio, 'uploads/p.hpgl', '/dev/x', 9600, 'None', analysis=analysis, pen_pause=True)
    assert sio.named('eta') == [] and sio.named('pen_change') == []


def test_drawn_seconds_are_recorded(app, start):
    thread, sio, _ = start()
    thread.join(10)
    assert app.globals.drawn_seconds is not None and 0 <= app.globals.drawn_seconds < 5


# ---- pen changes ------------------------------------------------------------------------------

@pytest.mark.parametrize('flow', ['CTS/RTS', 'Software', 'None', 'XON/XOFF', 'HP-IB'])
def test_plot_holds_at_a_pen_change_until_resumed(app, start, flow):
    thread, sio, analysis = start(flow=flow, pen_pause=True)
    boundary = app.hpgl.pen_changes(analysis)[0][0]
    assert wait_for(lambda: app.globals.wait_reason == 'pen_change')

    assert app.globals.paused is True and app.globals.wait_pen == 2
    assert sio.named('pen_change') == [{'pen': 2}]
    assert any('load pen 2' in line for line in sio.named('status_log'))
    time.sleep(0.2)
    port = app.serial.Serial.instances[-1]
    assert plot_data(port) == TWO_PENS[:boundary]            # exactly up to the new pen, nothing more
    assert b'PU;' in port.written                             # pen lifted while waiting
    assert thread.is_alive()

    app.globals.clear_wait()                                  # the Resume button
    thread.join(10)
    assert not thread.is_alive()
    assert plot_data(port) == TWO_PENS
    assert ('print_progress', {'data': 100}) in sio.events
    assert app.globals.wait_reason is None


def test_pen_pause_is_only_for_pen_changes(app, start):
    thread, sio, _ = start(content=THREE_PENS, pen_pause=True)
    for expected in (2, 3):
        assert wait_for(lambda: app.globals.wait_pen == expected and app.globals.paused)
        app.globals.clear_wait()
        assert wait_for(lambda: not app.globals.paused)
    thread.join(10)
    assert [e['pen'] for e in sio.named('pen_change')] == [2, 3]


def test_no_pause_when_pen_changes_are_automatic(app, start):
    thread, sio, _ = start(pen_pause=False)
    thread.join(10)
    assert sio.named('pen_change') == []
    assert plot_data(app.serial.Serial.instances[-1]) == TWO_PENS


def test_a_single_pen_never_pauses(app, start):
    thread, sio, _ = start(content=b'IN;SP1;' + LAYER * 20 + b'SP0;', pen_pause=True)
    thread.join(10)
    assert sio.named('pen_change') == []


def test_waiting_time_is_not_counted_as_drawing(app, start):
    started = time.time()
    thread, sio, _ = start(pen_pause=True)
    assert wait_for(lambda: app.globals.wait_reason == 'pen_change')
    time.sleep(0.6)
    app.globals.clear_wait()
    thread.join(10)
    assert time.time() - started - app.globals.drawn_seconds >= 0.55


def test_stop_ends_a_plot_waiting_for_a_pen(app, start):
    thread, sio, _ = start(pen_pause=True)
    assert wait_for(lambda: app.globals.wait_reason == 'pen_change')
    app.globals.printing = False                              # the stop button
    thread.join(10)
    assert not thread.is_alive()
    assert 'end_of_print' in [n for n, _ in sio.events]
    assert plot_data(app.serial.Serial.instances[-1]) != TWO_PENS


# ---- through the app --------------------------------------------------------------------------

def hpgl(uploads, name='a.hpgl', content=TWO_PENS):
    (uploads / name).write_bytes(content)


def test_analyze_route(client, uploads):
    hpgl(uploads)
    data = client.get('/analyze?file=a.hpgl').get_json()
    summary = data['summary']
    assert data['busy'] is False
    assert [p['pen'] for p in summary['pens']] == [1, 2]
    assert summary['seconds'] > 0 and summary['width_mm'] > 0 and summary['paths'] == 60


@pytest.mark.parametrize('query', ['', '?file=missing.hpgl', '?file=../x.hpgl', '?file=a.svg'])
def test_analyze_route_refuses_bad_files(client, uploads, query):
    hpgl(uploads)
    (uploads / 'a.svg').write_text('<svg/>')
    assert client.get('/analyze' + query).status_code == 400


def test_analyze_applies_the_correction(app, client, uploads):
    hpgl(uploads)
    plain = client.get('/analyze?file=a.hpgl').get_json()['summary']['seconds']
    for _ in range(3):
        job = app.history.start('x.hpgl', '/dev/x', 9600, 'None')
        app.history.set_estimate(job, 100)
        app.history.finish(job, 'completed', 100, drawn_s=200)
    assert client.get('/analyze?file=a.hpgl').get_json()['summary']['seconds'] == pytest.approx(plain * 2)


def test_analyze_does_not_compete_with_a_plot(app, client, uploads, monkeypatch):
    hpgl(uploads)
    app.main.plot_lock.acquire()
    try:
        assert client.get('/analyze?file=a.hpgl').get_json() == {'summary': None, 'busy': True}
        app.hpgl.analyze_cached('uploads/a.hpgl')              # once it is cached it can be read
        assert client.get('/analyze?file=a.hpgl').get_json()['summary']['paths'] == 60
    finally:
        app.main.plot_lock.release()


@pytest.fixture
def real_plot(app, client, uploads):
    """Plot through the app with the real sender, and wait for the plot to end."""
    def run(content=TWO_PENS, **form):
        hpgl(uploads, content=content)
        response = client.post('/start_plot', data={**PLOT, **form})
        return response
    return run


def finished(app):
    return wait_for(lambda: not app.main.plot_lock.locked())


def test_plot_records_estimate_and_drawing_time(app, client, real_plot):
    assert real_plot().data == b'Plot started'
    assert finished(app)
    [job] = client.get('/job_history').get_json()
    assert job['status'] == 'completed' and job['estimate_s'] > 0 and job['drawn_s'] >= 0


def test_pen_change_pauses_the_plot_for_every_page(app, client, real_plot):
    main = app.main
    watcher = main.socketio.test_client(main.app)
    watcher.get_received()
    assert real_plot(pen_change='pause').data == b'Plot started'
    assert wait_for(lambda: app.globals.wait_reason == 'pen_change')

    states = received(watcher, 'plot_state')
    assert states[-1]['paused'] is True and states[-1]['wait_reason'] == 'pen_change' and states[-1]['pen'] == 2
    [late] = received(main.socketio.test_client(main.app), 'plot_state')    # a page opened meanwhile
    assert late['wait_reason'] == 'pen_change' and late['pen'] == 2 and late['eta']['total'] > 0

    assert client.post('/resume_plot').data == b'Plot resumed'
    assert app.globals.wait_reason is None
    assert finished(app)
    assert client.get('/job_history').get_json()[0]['status'] == 'completed'


def test_stop_ends_a_plot_waiting_for_a_pen_change(app, client, real_plot):
    real_plot(pen_change='pause')
    assert wait_for(lambda: app.globals.wait_reason == 'pen_change')
    assert client.post('/stop_plot').data == b'Plot stopped'
    assert finished(app)
    assert client.get('/job_history').get_json()[0]['status'] == 'stopped'
    assert app.globals.wait_reason is None and app.globals.paused is False


def test_pen_change_default_comes_from_the_config(app, client, real_plot):
    assert client.post('/save_configfile', data={'plotter_pen_change': 'pause'}).status_code == 200
    real_plot()
    assert wait_for(lambda: app.globals.wait_reason == 'pen_change')
    client.post('/stop_plot')
    assert finished(app)


def test_invalid_pen_options_are_refused(client, real_plot):
    assert real_plot(pen_change='sometimes').status_code == 400
    for pens in ('x', '1;2', '1,', ',1', '123', '1\n'):
        assert real_plot(pens=pens).status_code == 400, pens
    assert real_plot(pens='7').status_code == 400                # the file has no pen 7


def test_plotting_only_some_pens(app, client, real_plot, monkeypatch):
    sent = []

    def capture(socketio, path, *args, **kwargs):
        sent.append((path, open(path, 'rb').read(), kwargs['analysis']))
        return True

    monkeypatch.setattr(app.send2serial, 'sendToPlotter', capture)
    assert real_plot(content=THREE_PENS, pens='1,3').data == b'Plot started'
    assert finished(app)
    [(path, data, analysis)] = sent
    assert b'SP2' not in data and b'SP1' in data and b'SP3' in data
    assert [s['pen'] for s in analysis['segments']] == [1, 3]       # the analysis is of the copy
    assert not os.path.exists(path)                                 # the copy is removed afterwards
    assert client.get('/job_history').get_json()[0]['file'] == 'a.hpgl'


def test_all_pens_means_the_original_file(app, client, real_plot, monkeypatch):
    sent = []
    monkeypatch.setattr(app.send2serial, 'sendToPlotter', lambda s, path, *a, **k: sent.append(path) or True)
    real_plot(pens='1,2')
    assert finished(app)
    assert sent == ['uploads/a.hpgl']


def test_a_file_that_cannot_be_prepared_fails_the_plot(app, client, real_plot, monkeypatch):
    monkeypatch.setattr(app.hpgl, 'MAX_ANALYSE_BYTES', 10)         # nothing can be analysed now
    assert real_plot(pens='1').status_code == 400                    # picking pens needs an analysis
    assert real_plot().data == b'Plot started'                       # a plain plot does not
    assert finished(app)
    assert client.get('/job_history').get_json()[0]['status'] == 'completed'
