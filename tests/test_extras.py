"""Plot-time settings, the area check, copies and queue order, the pen log, checkpoints and the
schema version of history.db, the file name guard, and the login throttle and HTTPS."""
import base64
import os
import sqlite3
import threading
import time

import pytest

from test_routes import PLOT, wait_for
from test_queue import add, names, queue, quick     # noqa: F401  (quick is a fixture)

FILE = b'IN;VS10;SP1;PU100,100;PD500,100,500,300;PR10,0,0,10;PU;SP2;PA0,0;PD50,50;PU;SP0;'


class SIO:
    def __init__(self):
        self.events = []

    def emit(self, name, data=None):
        self.events.append((name, data))

    def named(self, name):
        return [d['data'] for n, d in self.events if n == name]


# ---- plot-time settings: the file -----------------------------------------------------------

def tweak(app, tmp_path, content=FILE, **kwargs):
    src, dst = tmp_path / 'a.hpgl', tmp_path / 'b.hpgl'
    src.write_bytes(content)
    app.hpgl.tweak_file(str(src), str(dst), **kwargs)
    return dst.read_bytes()


def test_speed_force_and_acceleration_replace_the_files_own(app, tmp_path):
    out = tweak(app, tmp_path, speed=20, force=3, accel=2)
    assert out.startswith(b'IN;VS20;FS3;AS2;')
    assert out.count(b'VS') == 1                  # the file's VS10 is left out
    assert out.endswith(b'SP0;')


def test_only_what_is_set_is_changed(app, tmp_path):
    out = tweak(app, tmp_path, force=5)
    assert out == FILE.replace(b'IN;', b'IN;FS5;', 1)


def test_the_settings_follow_every_initialise(app, tmp_path):
    out = tweak(app, tmp_path, b'IN;SP1;PU1,1;IN;PU2,2;', speed=7)
    assert out == b'IN;VS7;SP1;PU1,1;IN;VS7;PU2,2;'


def test_a_file_without_initialise_gets_them_first(app, tmp_path):
    assert tweak(app, tmp_path, b'SP1;PU1,1;', speed=7) == b'VS7;SP1;PU1,1;'


def test_the_offset_moves_absolute_coordinates_only(app, tmp_path):
    out = tweak(app, tmp_path, dx=1000, dy=-50)
    assert b'PU1100,50;' in out
    assert b'PD1500,50,1500,250;' in out
    assert b'PR10,0,0,10;' in out                 # relative moves follow the first absolute one
    assert b'PA1000,-50;' in out                  # PA0,0 moved too


def test_the_offset_keeps_pen_state_and_arcs(app, tmp_path):
    out = tweak(app, tmp_path, b'IN;PU10,10;PD;PA20,20,30,30;AA0,0,90;PR;PU5,5;', dx=100, dy=100)
    assert out == b'IN;PU110,110;PD;PA120,120,130,130;AA100,100,90;PR;PU5,5;'


def test_a_file_that_scales_itself_cannot_be_moved(app, tmp_path):
    with pytest.raises(ValueError):
        tweak(app, tmp_path, b'IN;SC0,100,0,100;PU1,1;', dx=5)
    tweak(app, tmp_path, b'IN;SC0,100,0,100;PU1,1;', speed=5)         # without an offset it is fine


def test_labels_keep_their_end_mark(app, tmp_path):
    assert b'LBhi there\x03' in tweak(app, tmp_path, b'IN;PU1,1;LBhi there\x03PU;', dx=1)


def test_what_a_plot_drew_per_pen(app, tmp_path):
    path = tmp_path / 'a.hpgl'
    path.write_bytes(FILE)
    analysis = app.hpgl.analyze(str(path))
    whole = app.hpgl.pens_drawn(analysis)
    assert set(whole) == {1, 2}
    mm = analysis['units_per_mm']
    assert whole[1] == pytest.approx((400 + 200 + 10 + 10) / mm)       # pen 1: 400 + 200 units, then 10 + 10 relative
    assert whole[2] == pytest.approx(70.71 / mm, rel=0.01)
    # Halfway through pen 1's bytes: about half of its drawing; nothing of pen 2
    first = analysis['segments'][0]
    half = app.hpgl.pens_drawn(analysis, (first['start'] + first['end']) // 2)
    assert 2 not in half and 0 < half[1] < whole[1]
    assert app.hpgl.pens_drawn(analysis, 0) == {} and app.hpgl.pens_drawn(None) == {}


# ---- plot-time settings: the form -----------------------------------------------------------

@pytest.fixture
def plotter(app, uploads, monkeypatch):
    state = {'sent': [], 'kwargs': [], 'result': True}

    def fake_send(socketio, hpglfile, port, baud, flow, **kwargs):
        with open(hpglfile, 'rb') as f:
            state['sent'].append(f.read())
        state['kwargs'].append(kwargs)
        app.globals.sent_offset = 0
        return state['result']

    monkeypatch.setattr(app.send2serial, 'sendToPlotter', fake_send)
    (uploads / 'a.hpgl').write_bytes(FILE)
    return state


def run_plot(app, client, **extra):
    assert client.post('/start_plot', data=dict(PLOT, **extra)).data == b'Plot started'
    assert wait_for(lambda: not app.main.plot_lock.locked())


@pytest.mark.parametrize('field,value', [
    ('plot_speed', '0'), ('plot_speed', '101'), ('plot_speed', 'fast'), ('plot_speed', '1e1'),
    ('plot_force', '0'), ('plot_force', '129'), ('plot_force', '2.5'),
    ('plot_accel', '0'), ('plot_accel', '33'),
    ('offset_x', '501'), ('offset_y', '-501'), ('offset_x', '1,5'), ('offset_x', '१२'),
])
def test_bad_plot_settings_are_refused(app, client, plotter, field, value):
    assert client.post('/start_plot', data=dict(PLOT, **{field: value})).status_code == 400
    assert not app.main.plot_lock.locked() and plotter['sent'] == []


def test_the_settings_are_applied_to_the_file_that_is_sent(app, client, plotter):
    run_plot(app, client, plot_speed='12.5', plot_force='4', offset_x='10', offset_y='-5')
    sent = plotter['sent'][0]
    assert sent.startswith(b'IN;VS12.5;FS4;')
    dx = round(10 * app.hpgl.UNITS_PER_MM)
    assert b'PU%d,' % (100 + dx) in sent
    assert not os.path.exists(os.path.join('cache', 'plots', 'a-settings.hpgl'))      # the copy is deleted afterwards
    [job] = app.history.recent()
    assert job['options']['plot_speed'] == '12.5' and job['options']['offset_x'] == '10'


def test_without_settings_the_original_is_sent(app, client, plotter):
    run_plot(app, client)
    assert plotter['sent'][0] == FILE
    assert plotter['kwargs'][0]['frame'] is None


def test_settings_and_chosen_pens_go_together(app, client, plotter):
    run_plot(app, client, pens='2', plot_force='6')
    assert b'SP1' not in plotter['sent'][0] and plotter['sent'][0].startswith(b'IN;FS6;')


def test_a_plot_again_has_the_settings(app, client, plotter):
    run_plot(app, client, plot_speed='5')
    [job] = app.history.recent()
    assert client.post('/replot', data={'job': job['id']}).data == b'Plot started'
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert plotter['sent'][1].startswith(b'IN;VS5;')


def test_cal_files_take_no_hpgl_settings(app, client, uploads, plotter):
    (uploads / 'c.cal').write_text('R2;H;')
    data = dict(PLOT, file='c.cal', flowControl='XON/XOFF')
    assert client.post('/start_plot', data=dict(data, plot_speed='5')).status_code == 400
    assert client.post('/start_plot', data=dict(data, frame_check='on')).status_code == 400
    assert client.post('/start_plot', data=data).data == b'Plot started'


def test_a_resumed_plot_has_the_settings_too(app, client, plotter, monkeypatch):
    sent = []

    def fake_send(socketio, hpglfile, *args, **kwargs):
        sent.append(open(hpglfile, 'rb').read())
        app.globals.sent_offset = 30 if len(sent) == 1 else 0
        return False

    monkeypatch.setattr(app.send2serial, 'sendToPlotter', fake_send)
    run_plot(app, client, plot_force='6')
    [job] = app.history.recent()
    assert job['resume_offset'] == 30
    assert client.post('/resume_job', data={'job': job['id']}).data == b'Plot started'
    assert wait_for(lambda: not app.main.plot_lock.locked())
    # The preamble is made from the copy with the settings, and so is the rest
    assert sent[1].startswith(b'IN;FS6;VS10;') and sent[0].startswith(b'IN;FS6;VS10;')


# ---- the area check ---------------------------------------------------------------------------

def test_the_frame_check_gives_the_sender_the_drawings_bounds(app, client, plotter):
    run_plot(app, client, frame_check='on')
    frame = plotter['kwargs'][0]['frame']
    assert frame == [0.0, 0.0, 510.0, 310.0]        # the lowest and highest points of every drawn line


def test_the_frame_moves_with_the_offset(app, client, plotter):
    run_plot(app, client, frame_check='on', offset_x='10')
    dx = round(10 * app.hpgl.UNITS_PER_MM)
    assert plotter['kwargs'][0]['frame'] == [dx, 0.0, 510 + dx, 310.0]


def test_no_frame_when_resuming(app, client, plotter, monkeypatch):
    plotter['result'] = False
    monkeypatch.setattr(app.send2serial, 'sendToPlotter', lambda socketio, hpglfile, *a, **k: (
        plotter['kwargs'].append(k), setattr(app.globals, 'sent_offset', 40), False)[2])
    run_plot(app, client, frame_check='on')
    [job] = app.history.recent()
    assert client.post('/resume_job', data={'job': job['id']}).data == b'Plot started'
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert plotter['kwargs'][0]['frame'] is not None and plotter['kwargs'][1]['frame'] is None


@pytest.fixture
def sender(app, uploads):
    threads = []

    def run(frame, flow='None'):
        (uploads / 'p.hpgl').write_bytes(FILE)
        sio = SIO()
        thread = threading.Thread(target=lambda: app.send2serial.sendToPlotter(
            sio, 'uploads/p.hpgl', '/dev/x', 9600, flow, frame=frame), daemon=True)
        threads.append(thread)
        thread.start()
        return thread, sio
    yield run
    app.globals.printing = False
    for thread in threads:
        thread.join(5)
    leaked = [thread for thread in threads if thread.is_alive()]
    assert not leaked, 'a sender thread did not end: it would leak into the next test'


def test_the_area_is_traced_and_the_plot_waits_for_resume(app, sender):
    thread, sio = sender([100, 200, 300, 400])
    assert wait_for(lambda: app.globals.wait_reason == 'frame_check')
    time.sleep(0.2)
    port = app.serial.Serial.instances[-1]
    written = b''.join(port.written)
    assert b'PU;PA100,200;PA300,200;PA300,400;PA100,400;PA100,200;PU;' in written
    assert b'SP1' not in written                           # nothing of the file yet
    assert sio.named('wait_change') == [{'reason': 'frame_check'}]
    assert app.globals.paused is True

    app.globals.clear_wait()                               # Resume
    thread.join(10)
    written = b''.join(port.written)
    assert FILE in written and written.index(b'PA100,200') < written.index(FILE)
    assert ('end_of_print', {'data': 'True'}) in sio.events


def test_stop_while_waiting_for_the_area_check(app, sender):
    thread, sio = sender([1, 2, 3, 4])
    assert wait_for(lambda: app.globals.wait_reason == 'frame_check')
    app.globals.printing = False
    thread.join(5)
    assert not thread.is_alive() and FILE not in b''.join(app.serial.Serial.instances[-1].written)


def test_the_area_check_does_not_move_the_progress(app, sender):
    thread, sio = sender([1, 2, 3, 4])
    assert wait_for(lambda: app.globals.wait_reason == 'frame_check')
    assert app.globals.sent_offset == 0                   # the trace is not part of the file
    app.globals.clear_wait()
    thread.join(10)
    assert app.globals.sent_offset == len(FILE)


# ---- copies and the order of the queue -------------------------------------------------------

def test_copies_are_queued_with_a_paper_change_between(app, client, quick):
    assert add(client, copies='3', paper_between='on').data == b'Added 3 copies to the queue'
    items = queue(client)['items']
    assert [i['file'] for i in items] == ['a.hpgl'] * 3
    assert [i['pause_after'] for i in items] == [True, True, False]


def test_copies_without_paper_changes(app, client, quick):
    add(client, copies='2', pause_after='on')
    assert [i['pause_after'] for i in queue(client)['items']] == [False, True]


@pytest.mark.parametrize('copies', ['0', '21', 'x', '-1', '1.5', ''])
def test_copies_are_checked(app, client, quick, copies):
    response = add(client, copies=copies)
    assert (response.status_code == 200) == (copies == '')       # empty means one
    assert len(names(client)) == (1 if copies == '' else 0)


def test_copies_must_fit_in_the_queue(app, client, quick, monkeypatch):
    monkeypatch.setattr(app.queue, 'MAX_ITEMS', 3)
    add(client)
    assert add(client, copies='3').status_code == 400
    assert len(names(client)) == 1                                # nothing was half added
    assert add(client, copies='2').status_code == 200


def test_the_queue_runs_the_copies(app, client, quick):
    add(client, copies='2')
    assert client.post('/queue/start').status_code == 200
    assert wait_for(lambda: not app.globals.queue_active and not app.main.plot_lock.locked())
    assert quick['sent'] == ['a.hpgl', 'a.hpgl'] and names(client) == []


def test_the_queue_is_put_in_a_new_order(app, client, quick):
    for name in ('a.hpgl', 'b.hpgl', 'c.hpgl'):
        add(client, name)
    ids = [i['id'] for i in queue(client)['items']]
    order = [ids[2], ids[0], ids[1]]
    assert client.post('/queue/order', data={'ids': ','.join(str(i) for i in order)}).status_code == 200
    assert names(client) == ['c.hpgl', 'a.hpgl', 'b.hpgl']


@pytest.mark.parametrize('ids', ['', 'x', '1,1', '1,2', '-1,2,3', '1,2,3,4'])
def test_a_wrong_order_is_refused(app, client, quick, ids):
    for name in ('a.hpgl', 'b.hpgl', 'c.hpgl'):
        add(client, name)
    real = [i['id'] for i in queue(client)['items']]
    ids = ids.replace('1,2,3,4', ','.join(str(i) for i in real + [real[-1] + 1]))
    assert client.post('/queue/order', data={'ids': ids}).status_code in (400, 409)
    assert names(client) == ['a.hpgl', 'b.hpgl', 'c.hpgl']
    assert client.get('/queue/order').status_code == 405


def test_the_running_plot_stays_out_of_the_order(app, client, quick):
    for name in ('a.hpgl', 'b.hpgl', 'c.hpgl'):
        add(client, name)
    ids = [i['id'] for i in queue(client)['items']]
    app.queue.set_status(ids[0], app.queue.RUNNING)
    assert client.post('/queue/order', data={'ids': ','.join(str(i) for i in ids)}).status_code == 409   # lists the running one
    assert client.post('/queue/order', data={'ids': '{},{}'.format(ids[2], ids[1])}).status_code == 200
    assert names(client) == ['a.hpgl', 'c.hpgl', 'b.hpgl']


# ---- the pen log -------------------------------------------------------------------------------

def test_a_completed_plot_adds_to_the_pen_log(app, client, plotter, monkeypatch):
    run_plot(app, client)
    pens = {p['pen']: p for p in client.get('/pen_usage').get_json()['pens']}
    assert set(pens) == {1, 2} and pens[1]['mm'] > 0 and pens[1]['plots'] == 1
    run_plot(app, client)
    pens2 = {p['pen']: p for p in client.get('/pen_usage').get_json()['pens']}
    assert pens2[1]['plots'] == 2 and pens2[1]['mm'] == pytest.approx(2 * pens[1]['mm'])


def test_a_stopped_plot_counts_what_was_sent(app, client, plotter, monkeypatch):
    plotter['result'] = False

    def partial(socketio, hpglfile, *args, **kwargs):
        app.globals.sent_offset = FILE.index(b'SP2')       # all of pen 1, none of pen 2
        return False

    monkeypatch.setattr(app.send2serial, 'sendToPlotter', partial)
    run_plot(app, client)
    pens = {p['pen'] for p in client.get('/pen_usage').get_json()['pens']}
    assert pens == {1}


def test_a_plot_that_sent_nothing_does_not_count(app, client, plotter):
    plotter['result'] = False
    run_plot(app, client)
    assert client.get('/pen_usage').get_json() == {'pens': []}


def test_a_pen_is_reset_when_it_is_replaced(app, client, plotter):
    run_plot(app, client)
    assert client.post('/pen_usage/reset', data={'pen': '1'}).status_code == 200
    assert {p['pen'] for p in client.get('/pen_usage').get_json()['pens']} == {2}
    assert client.post('/pen_usage/reset', data={'pen': '1'}).status_code == 404
    assert client.post('/pen_usage/reset', data={'pen': 'x'}).status_code == 400
    assert client.get('/pen_usage/reset').status_code == 405


def test_the_pen_log_is_in_the_backup(app, client, plotter):
    import io
    import zipfile
    run_plot(app, client)
    data = client.get('/backup').data
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive.extract('history.db', app.dir)
    conn = sqlite3.connect(app.dir / 'history.db')
    assert conn.execute('SELECT COUNT(*) FROM pen_usage').fetchone()[0] == 2
    conn.close()


# ---- checkpoints and plots cut short ---------------------------------------------------------

def test_a_running_plot_notes_where_it_has_got_to(app, client, uploads, monkeypatch):
    monkeypatch.setattr(app.main, 'CHECKPOINT_EVERY', 0.02)
    monkeypatch.setattr(app.main, 'CHECKPOINT_MIN_BYTES', 10)
    release = {'go': False}

    def fake_send(socketio, hpglfile, *args, **kwargs):
        app.globals.printing = True
        app.globals.sent_offset = 40
        app.globals.buffer_used = 5
        while not release['go'] and app.globals.printing:
            time.sleep(0.005)
        return False

    monkeypatch.setattr(app.send2serial, 'sendToPlotter', fake_send)
    (uploads / 'a.hpgl').write_bytes(FILE)
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: app.history.recent() and app.history.recent()[0]['resume_offset'] == 35)
    assert app.history.recent()[0]['status'] == 'running'
    release['go'] = True
    assert wait_for(lambda: not app.main.plot_lock.locked())


def test_a_checkpoint_never_touches_a_finished_plot(app):
    job = app.history.start('a.hpgl', '/dev/x', 9600, 'None')
    app.history.finish(job, 'completed', 100)
    app.history.checkpoint(job, 99)
    assert app.history.get(job)['resume_offset'] is None
    app.history.checkpoint(None, 5)         # nothing to do, and no error


def test_a_plot_cut_short_by_a_restart_can_be_resumed(app, client, uploads, monkeypatch):
    (uploads / 'a.hpgl').write_bytes(FILE)
    job = app.history.start('a.hpgl', '/dev/ttyAMA0', 9600, 'CTS/RTS', dict(PLOT), len(FILE))
    app.history.checkpoint(job, 40)
    app.history.init()                      # the server starts again: the plot was running
    row = app.history.get(job)
    assert row['status'] == 'interrupted' and row['resume_offset'] == 40
    rows = client.get('/job_history').get_json()
    assert rows[0]['can_resume'] is True

    sent = []
    monkeypatch.setattr(app.send2serial, 'sendToPlotter', lambda socketio, hpglfile, *a, **k: (sent.append(open(hpglfile, 'rb').read()), True)[1])
    assert client.post('/resume_job', data={'job': job, 'rewind': '4096'}).status_code == 200
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert sent[0] == FILE                  # rewound to the start: nothing is skipped


def test_an_interrupted_plot_without_a_checkpoint_cannot_be_resumed(app, client, uploads):
    (uploads / 'a.hpgl').write_bytes(FILE)
    job = app.history.start('a.hpgl', '/dev/ttyAMA0', 9600, 'CTS/RTS', dict(PLOT), len(FILE))
    app.history.init()
    assert client.get('/job_history').get_json()[0]['can_resume'] is False


# ---- the schema version of history.db ---------------------------------------------------------

def test_a_new_database_has_the_current_version(app):
    with app.history._connect() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == app.history.SCHEMA_VERSION >= 1


def test_a_database_from_before_versions_is_brought_up(app, tmp_path, monkeypatch):
    path = str(tmp_path / 'old.db')
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, file TEXT NOT NULL, port TEXT, baudrate INTEGER, '
                 'flow_control TEXT, started_at REAL NOT NULL, finished_at REAL, status TEXT NOT NULL, '
                 'progress INTEGER DEFAULT 0, error TEXT)')
    conn.execute("INSERT INTO jobs (file, started_at, status) VALUES ('old.hpgl', 1, 'completed')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(app.history, 'DB_PATH', path)
    app.history.init()
    assert app.history.problem is None and app.history.recent()[0]['file'] == 'old.hpgl'
    with app.history._connect() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == app.history.SCHEMA_VERSION


def test_migrations_run_once_and_in_order(app, tmp_path, monkeypatch):
    path = str(tmp_path / 'm.db')
    ran = []
    first = app.history._migration_1

    def second(conn):
        ran.append(2)
        conn.execute('CREATE TABLE extra (x INTEGER)')

    monkeypatch.setattr(app.history, 'DB_PATH', path)
    monkeypatch.setattr(app.history, 'MIGRATIONS', (first,))
    monkeypatch.setattr(app.history, 'SCHEMA_VERSION', 1)
    app.history.init()
    monkeypatch.setattr(app.history, 'MIGRATIONS', (first, second))
    monkeypatch.setattr(app.history, 'SCHEMA_VERSION', 2)
    app.history.init()
    app.history.init()
    assert ran == [2]


def test_a_newer_database_is_left_alone(app, tmp_path, monkeypatch, capsys):
    path = str(tmp_path / 'new.db')
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE jobs (id INTEGER PRIMARY KEY, file TEXT)')
    conn.execute('PRAGMA user_version = 99')
    conn.commit()
    conn.close()
    monkeypatch.setattr(app.history, 'DB_PATH', path)
    try:
        app.history.init()
        assert 'newer web plotter' in app.history.problem
        assert app.history.recent() == []                       # reports an error instead of half using it
        assert app.history.start('a.hpgl', '/dev/x', 9600, 'None') is None
        assert app.queue.add({'file': 'a.hpgl'}) is None
        app.pen_usage.add({1: 5})                               # none of these may raise
    finally:
        app.history.DB_PATH = 'history.db'
        app.history.init()
    assert app.history.problem is None
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 99     # untouched
    assert 'newer web plotter' in capsys.readouterr().out


# ---- the converted file's name ----------------------------------------------------------------

def test_a_too_long_converted_name_is_refused(app, client, uploads, monkeypatch):
    (uploads / 'a.svg').write_text('<svg/>')
    from test_routes import CONVERT
    monkeypatch.setattr(app.convert_stub, 'output_name', lambda file, *a, **k: 'x' * 230 + '.hpgl', raising=False)
    monkeypatch.setattr(app.main, 'output_name', lambda file, *a, **k: 'x' * 230 + '.hpgl')
    for route in ('/start_conversion', '/preview_conversion'):
        response = client.post(route, data=CONVERT)
        assert response.status_code == 400 and b'at most 200' in response.data
    assert app.convert_stub.calls == []
    monkeypatch.setattr(app.main, 'output_name', lambda file, *a, **k: 'x' * 190 + '.hpgl')
    assert client.post('/start_conversion', data=CONVERT).status_code == 200


def test_only_one_conversion_runs_at_a_time(app, client, uploads, monkeypatch):
    from test_routes import CONVERT
    (uploads / 'a.svg').write_text('<svg/>')
    started, release = threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        started.set()
        release.wait(5)
        return 'Exported'

    monkeypatch.setattr(app.main, 'run_conversion', slow)
    result = {}
    thread = threading.Thread(target=lambda: result.update(r=client.post('/start_conversion', data=CONVERT)), daemon=True)
    thread.start()
    assert started.wait(3)
    for route in ('/start_conversion', '/preview_conversion'):
        assert client.post(route, data=CONVERT).status_code == 409
    release.set()
    thread.join(5)
    assert result['r'].status_code == 200
    assert client.post('/start_conversion', data=CONVERT).status_code == 200      # the lock was let go


# ---- the login ------------------------------------------------------------------------------------

def auth(credentials):
    return {'Authorization': 'Basic ' + base64.b64encode(credentials.encode()).decode()}


@pytest.fixture
def login(app):
    app.main.config.add_section('auth')
    app.main.config['auth']['username'] = 'u'
    app.main.config['auth']['password'] = 'p'


def test_too_many_wrong_passwords_lock_the_address_out(app, client, login):
    for _ in range(app.auth_throttle.MAX_FAILURES):
        assert client.get('/', headers=auth('u:wrong')).status_code in (401, 429)
    response = client.get('/', headers=auth('u:p'))               # even the right one is refused now
    assert response.status_code == 429 and int(response.headers['Retry-After']) > 0


def test_a_request_without_a_login_is_not_counted(app, client, login):
    for _ in range(app.auth_throttle.MAX_FAILURES * 2):
        assert client.get('/').status_code == 401
    assert client.get('/', headers=auth('u:p')).status_code == 200


def test_a_right_password_forgets_the_wrong_ones(app, client, login):
    for _ in range(app.auth_throttle.MAX_FAILURES - 1):
        client.get('/', headers=auth('u:wrong'))
    assert client.get('/', headers=auth('u:p')).status_code == 200
    for _ in range(app.auth_throttle.MAX_FAILURES - 1):
        client.get('/', headers=auth('u:wrong'))
    assert client.get('/', headers=auth('u:p')).status_code == 200


def test_the_lockout_ends(app):
    t = app.auth_throttle
    assert not any(t.failed('1.2.3.4', now=100 + i) for i in range(t.MAX_FAILURES - 1))
    assert t.failed('1.2.3.4', now=110) is True
    assert t.blocked('1.2.3.4', now=111) > 0 and t.blocked('5.6.7.8', now=111) == 0
    assert t.blocked('1.2.3.4', now=110 + t.LOCKOUT + 1) == 0
    assert t.failed('1.2.3.4', now=110 + t.LOCKOUT + 2) is False       # a fresh start


def test_old_failures_do_not_add_up(app):
    t = app.auth_throttle
    for i in range(t.MAX_FAILURES - 1):
        t.failed('a', now=i)
    assert t.failed('a', now=t.WINDOW + 100) is False


def test_the_table_of_addresses_cannot_grow_without_bound(app):
    t = app.auth_throttle
    for i in range(t.MAX_TRACKED + 50):
        t.failed('10.0.%d.%d' % (i // 256, i % 256), now=1000)
    assert len(t._failures) <= t.MAX_TRACKED


# ---- HTTPS ---------------------------------------------------------------------------------------

def test_https_needs_both_files(app, tmp_path):
    cert, key = tmp_path / 'c.pem', tmp_path / 'k.pem'
    config = app.main.config
    assert app.main.https_context() is None                       # nothing set: plain HTTP
    config.set('server', 'ssl_certificate', str(cert))
    config.set('server', 'ssl_key', str(key))
    assert app.main.https_context() is None                       # files missing: plain HTTP, not a crash
    cert.write_text('x')
    assert app.main.https_context() is None
    key.write_text('x')
    assert app.main.https_context() == (str(cert), str(key))
    config.set('server', 'ssl_key', '')
    assert app.main.https_context() is None


@pytest.mark.parametrize('value,ok', [
    ('', True), ('/home/pi/cert.pem', True), ('cert.pem', False), ('/a/../etc/x', False),
    ('/a\\b', False), ('/a\nb', False), ('/' + 'x' * 300, False),
])
def test_the_https_paths_are_checked(app, value, ok):
    for field in ('server_ssl_certificate', 'server_ssl_key'):
        assert bool(app.main.CONFIG_FIELDS[field][2](value)) is ok
