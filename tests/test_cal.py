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
