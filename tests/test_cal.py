"""CalComp .cal files: uploaded and sent as a plain stream, with none of the HP-GL set-up or queries."""
import io

import pytest

from test_routes import PLOT, wait_for
from test_send2serial import SIO

CAL = b'R2;H;\nF1;\nF10,16;\nC985,1200;K;I;C1885,1200;K;\nH;\nF2;\nC985,1000;K;I;C1885,1000;K;\nH;F0;\n' * 20
CAL_PLOT = dict(PLOT, file='a.cal', flowControl='XON/XOFF')


def send(app, uploads, flow, content=CAL, name='t.cal'):
    (uploads / name).write_bytes(content)
    sio = SIO()
    result = app.send2serial.sendToPlotter(sio, 'uploads/' + name, '/dev/x', 9600, flow)
    return result, sio


def test_cal_files_can_be_uploaded(client, uploads):
    response = client.post('/', data={'file': (io.BytesIO(b'R2;H;'), 'Job.CAL')}, content_type='multipart/form-data')
    assert response.status_code == 204 and (uploads / 'Job.cal').exists()
    assert (uploads / 'Job.cal').name in [f['name'] for f in client.get('/update_files').get_json()['content']]


@pytest.mark.parametrize('flow', ['XON/XOFF', 'None', 'NONE'])
def test_the_file_is_sent_unchanged_without_any_hpgl(app, uploads, flow):
    result, sio = send(app, uploads, flow)
    port = app.serial.Serial.instances[0]
    sent = b''.join(port.written)

    assert result is True and port.closed
    assert sent == CAL                                           # the file as it is (it ends with H;F0;)
    assert b'IN;' not in sent and b'\033' not in sent and b'OI;' not in sent
    assert ('print_progress', {'data': 100}) in sio.events and 'end_of_print' in sio.names()
    assert app.globals.current_file == 'None'


def test_xon_xoff_is_still_used_for_the_port(app, uploads):
    send(app, uploads, 'XON/XOFF')
    assert app.serial.Serial.instances[0].kwargs['xonxoff'] is True


@pytest.mark.parametrize('flow', ['CTS/RTS', 'Software', 'HP-IB'])
def test_flow_control_that_asks_the_plotter_questions_is_refused(app, uploads, flow):
    result, sio = send(app, uploads, flow)
    assert result is False and app.serial.Serial.instances == []
    assert '.cal' in sio.events[0][1]['data']


def test_stop_lifts_the_pen_but_sends_no_hpgl(app, uploads):
    Serial = app.serial.Serial
    seen = {'chunks': 0}

    def stop(port, data):
        seen['chunks'] += 1
        if seen['chunks'] == 5:
            app.globals.printing = False

    Serial.on_data = stop
    result, _ = send(app, uploads, 'XON/XOFF', CAL * 20)
    written = Serial.instances[0].written

    assert result is True and seen['chunks'] < 50
    assert written[-1] == b'H;' and not any(b'PU;' in w or b'\033' in w for w in written)


def test_a_lost_connection_fails_the_plot_instead_of_guessing(app, uploads):
    app.serial.Serial.break_at = 5
    result, sio = send(app, uploads, 'XON/XOFF')
    assert result is False and len(app.serial.Serial.instances) == 1
    assert any(n == 'error' and 'Lost the connection' in d['data'] for n, d in sio.events)
    assert app.globals.printing is False


# ---- through the web page -------------------------------------------------------------------------

def test_a_cal_plot_is_started_and_recorded(app, client, uploads, monkeypatch):
    calls = []

    def fake_send(socketio, path, port, baud, flow, **kwargs):
        calls.append((path, flow, kwargs.get('analysis'), kwargs.get('pen_pause')))
        app.globals.sent_offset = 100
        return False                                  # failed part way: still nothing to resume

    monkeypatch.setattr(app.send2serial, 'sendToPlotter', fake_send)
    (uploads / 'a.cal').write_bytes(CAL)
    assert client.post('/start_plot', data=dict(CAL_PLOT, pen_change='pause')).data == b'Plot started'
    assert wait_for(lambda: calls and not app.main.plot_lock.locked())

    assert calls == [('uploads/a.cal', 'XON/XOFF', None, False)]      # no analysis, no pen pauses
    [job] = client.get('/job_history').get_json()
    assert job['file'] == 'a.cal' and job['can_replot'] is True
    assert job['can_resume'] is False and app.history.get(job['id'])['resume_offset'] is None
    assert client.post('/resume_job', data={'job': job['id']}).status_code == 400


def test_a_cal_plot_can_be_plotted_again(app, client, uploads, monkeypatch):
    calls = []
    monkeypatch.setattr(app.send2serial, 'sendToPlotter',
                        lambda socketio, path, port, baud, flow, **kw: calls.append((path, flow)) or True)
    (uploads / 'a.cal').write_bytes(CAL)
    client.post('/start_plot', data=CAL_PLOT)
    assert wait_for(lambda: len(calls) == 1 and not app.main.plot_lock.locked())
    [job] = client.get('/job_history').get_json()
    assert client.post('/replot', data={'job': job['id']}).data == b'Plot started'
    assert wait_for(lambda: len(calls) == 2 and not app.main.plot_lock.locked())
    assert calls[0] == calls[1] == ('uploads/a.cal', 'XON/XOFF')


@pytest.mark.parametrize('extra, text', [
    ({'flowControl': 'CTS/RTS'}, 'XON/XOFF'),
    ({'flowControl': 'Software'}, 'XON/XOFF'),
    ({'pens': '1'}, 'Pens'),
])
def test_invalid_cal_plots_are_refused(client, uploads, extra, text):
    (uploads / 'a.cal').write_bytes(CAL)
    response = client.post('/start_plot', data=dict(CAL_PLOT, **extra))
    assert response.status_code == 400 and text in response.get_data(as_text=True)


def test_cal_files_can_be_queued(client, uploads):
    (uploads / 'a.cal').write_bytes(CAL)
    assert client.post('/queue/add', data=CAL_PLOT).status_code == 200
    assert client.post('/queue/add', data=dict(CAL_PLOT, flowControl='CTS/RTS')).status_code == 400
    assert [i['file'] for i in client.get('/queue').get_json()['items']] == ['a.cal']


@pytest.mark.parametrize('route, data', [('/analyze', None), ('/plotter/bounds', {'file': 'a.cal'})])
def test_hpgl_only_features_refuse_cal(client, uploads, route, data):
    (uploads / 'a.cal').write_bytes(CAL)
    if data is None:
        response = client.get(route + '?file=a.cal')
    else:
        response = client.post(route, data=dict(data, port='/dev/ttyAMA0', baudrate='9600', flowControl='XON/XOFF'))
    assert response.status_code == 400


def test_a_backup_keeps_cal_files(client, uploads):
    (uploads / 'a.cal').write_bytes(CAL)
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(client.get('/backup?uploads=1').data)).namelist()
    assert 'uploads/a.cal' in names


# ---- CalComp flow control: ask the plotter how full its buffer is -------------------------------------

POLL = b'\x11'


class SimBuffer:
    """A Model 84's 1024-byte input buffer. Plot data fills it; each status request lets it drain by `drain` bytes
    (what the plotter draws between two requests), so it is slow enough to fill up. `hysteresis` makes "full" last
    from 75 % until it is below 25 %; otherwise the answer follows the level. Data that arrives when the buffer is
    full is lost: that is what makes a stray coordinate."""

    def __init__(self, app, drain=120, hysteresis=False):
        self.used, self.drain, self.hyst, self.full = 0, drain, hysteresis, False
        self.max_used, self.dropped, self.got = 0, 0, bytearray()
        app.serial.Serial.on_write = self.on_data
        app.serial.Serial.status_answer = self.answer

    def on_data(self, port, data):
        for byte in data:
            if self.used >= 1024:
                self.dropped += 1
                continue
            self.used += 1
            self.got.append(byte)
        self.max_used = max(self.max_used, self.used)
        if self.used >= 768:
            self.full = True

    def answer(self, port):
        self.used = max(0, self.used - self.drain)
        if self.used < 256:
            self.full = False
        full = self.full if self.hyst else self.used >= 256
        return b'\x1a' if full else b'\x01'


@pytest.mark.parametrize('hysteresis', [False, True])
@pytest.mark.parametrize('drain', [30, 120, 600])
def test_calcomp_flow_never_overflows_the_plotters_buffer(app, uploads, hysteresis, drain):
    sim = SimBuffer(app, drain=drain, hysteresis=hysteresis)
    result, sio = send(app, uploads, 'CalComp', CAL * 2)

    assert result is True and sim.dropped == 0 and sim.max_used < 1024
    assert bytes(sim.got) == CAL * 2                                # every byte, in order, none repeated
    assert app.serial.Serial.polls > len(CAL * 2) // 256            # asked before every 256 bytes
    assert ('print_progress', {'data': 100}) in sio.events


def test_the_same_data_would_overflow_without_asking(app, uploads):
    """The control case: with answers always "empty" (as with XON/XOFF, where nothing is asked) the buffer overflows."""
    sim = SimBuffer(app, drain=30)
    app.serial.Serial.status_answer = None                           # always "empty"
    result, _ = send(app, uploads, 'XON/XOFF', CAL * 2)
    assert result is True and sim.dropped > 0


def test_calcomp_flow_opens_the_port_without_driver_handshaking_and_sends_no_hpgl(app, uploads):
    send(app, uploads, 'CalComp')
    port = app.serial.Serial.instances[0]
    sent = b''.join(w for w in port.written if w != POLL)
    assert sent == CAL and port.closed
    assert not port.kwargs.get('xonxoff') and not port.kwargs.get('rtscts') and port.kwargs['timeout'] <= 0.2
    assert b'IN;' not in sent and b'\033' not in sent


def test_a_plotter_that_does_not_answer_is_reported_before_any_data_is_sent(app, uploads, monkeypatch):
    monkeypatch.setattr(app.send2serial, 'CAL_ANSWER_S', 0.05)
    app.serial.Serial.no_reply = True
    result, sio = send(app, uploads, 'CalComp')
    port = app.serial.Serial.instances[0]
    assert result is False and port.written == [POLL] and port.closed
    assert any(n == 'error' and 'did not answer' in d['data'] and 'XON/XOFF' in d['data'] for n, d in sio.events)
    assert app.globals.printing is False


def test_stop_while_the_plotter_says_full_lifts_the_pen(app, uploads):
    Serial = app.serial.Serial

    def full(port):
        if Serial.polls >= 4:
            app.globals.printing = False
        return b'\x1a'

    Serial.status_answer = full
    result, _ = send(app, uploads, 'CalComp')
    written = Serial.instances[0].written
    assert result is True and written[-1] == b'H;' and Serial.polls == 4
    assert not any(w not in (POLL, b'H;') for w in written)          # nothing of the file was sent


def test_calcomp_flow_is_only_for_cal_files(app, client, uploads):
    (uploads / 'a.hpgl').write_bytes(b'IN;SP1;PU0,0;PD100,100;SP0;')
    result, sio = send(app, uploads, 'CalComp', b'IN;PU0,0;', name='h.hpgl')
    assert result is False and app.serial.Serial.instances == []
    assert any('for .cal files' in d['data'] for n, d in sio.events if n == 'error')
    response = client.post('/start_plot', data=dict(PLOT, flowControl='CalComp'))
    assert response.status_code == 400 and 'for .cal files' in response.get_data(as_text=True)
    assert client.post('/queue/add', data=dict(PLOT, flowControl='CalComp')).status_code == 400


def test_calcomp_flow_is_accepted_for_cal_plots_and_queued(app, client, uploads, monkeypatch):
    calls = []
    monkeypatch.setattr(app.send2serial, 'sendToPlotter',
                        lambda socketio, path, port, baud, flow, **kw: calls.append((path, flow)) or True)
    (uploads / 'a.cal').write_bytes(CAL)
    data = dict(CAL_PLOT, flowControl='CalComp')
    assert client.post('/start_plot', data=data).data == b'Plot started'
    assert wait_for(lambda: calls and not app.main.plot_lock.locked())
    assert calls == [('uploads/a.cal', 'CalComp')]
    assert client.post('/queue/add', data=data).status_code == 200
    assert 'CalComp' in app.main.FLOW_CONTROLS


def test_the_flow_control_lists_offer_calcomp():
    import pathlib
    html = (pathlib.Path(__file__).resolve().parent.parent / 'templates' / 'index.html').read_text(encoding='utf-8')
    assert html.count('<option value="CalComp">') == 2               # the plot form and the settings dialog
