import base64
import io
import time

import pytest
import requests

PLOT = dict(file='a.hpgl', port='/dev/ttyAMA0', baudrate='9600', flowControl='CTS/RTS')
CONVERT = dict(file='a.svg', outputsize='a4', pageorientation='portrait', device='hp7475a', speed='')


def wait_for(condition, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def slow_plot(app, monkeypatch):
    """Replace the real plotting with one that runs until released, like a long plot."""
    state = {'release': False}

    def fake_send(socketio, hpglfile, port, baud, flow):
        app.globals.printing = True
        while app.globals.printing and not state['release']:
            time.sleep(0.005)
        return True

    monkeypatch.setattr(app.send2serial, 'sendToPlotter', fake_send)
    yield state
    state['release'] = True
    wait_for(lambda: not app.main.plot_lock.locked())


def test_index_renders(client):
    assert client.get('/').status_code == 200


# ---- file name handling ---------------------------------------------------------------------

@pytest.mark.parametrize('name', ['../secret.txt', '../../etc/passwd', '/etc/passwd', 'sub/../../x'])
def test_path_traversal_is_refused(client, uploads, name):
    (uploads.parent / 'secret.txt').write_text('x')
    assert client.post('/delete_file', json={'filename': name}).status_code == 404
    assert (uploads.parent / 'secret.txt').exists()
    assert client.post('/start_plot', data={**PLOT, 'file': name}).status_code == 400
    assert client.post('/start_conversion', data={**CONVERT, 'file': name}).status_code == 400


def test_upload_normalises_and_validates(client, uploads):
    def upload(name):
        return client.post('/', data={'file': (io.BytesIO(b'x'), name)}, content_type='multipart/form-data')

    assert upload('Foo.SVG').status_code == 204
    assert (uploads / 'Foo.svg').exists()
    assert upload('x.exe').status_code == 400
    assert upload('.svg').status_code == 400
    assert client.post('/', data={}, content_type='multipart/form-data').status_code == 400


def test_delete_file(client, uploads):
    (uploads / 'a.hpgl').write_text('IN;')
    assert client.post('/delete_file', json={'filename': 'a.hpgl'}).data == b'Deleted: a.hpgl'
    assert not (uploads / 'a.hpgl').exists()
    assert client.post('/delete_file', json={'filename': 'a.hpgl'}).status_code == 404


# ---- request protection ---------------------------------------------------------------------

def test_cross_site_post_is_refused(client):
    assert client.post('/action_tasmota', headers={'Origin': 'http://evil.example'}).status_code == 403
    assert client.post('/action_tasmota', headers={'Origin': 'http://localhost'}).status_code != 403


def test_state_changing_routes_are_post_only(client):
    assert client.get('/stop_plot').status_code == 405
    assert client.get('/update_baud').status_code == 405
    assert client.get('/delete_file').status_code == 405


def test_basic_auth(app, client):
    main = app.main
    main.config.add_section('auth')
    main.config['auth']['username'] = 'u'
    main.config['auth']['password'] = 'p'

    def auth(credentials):
        return {'Authorization': 'Basic ' + base64.b64encode(credentials.encode()).decode()}

    assert client.get('/').status_code == 401
    assert client.get('/', headers=auth('u:wrong')).status_code == 401
    assert client.get('/', headers=auth('u:p')).status_code == 200
    # Socket.IO connections bypass before_request, so they are checked separately
    assert not main.socketio.test_client(main.app).is_connected()


# ---- conversion -----------------------------------------------------------------------------

def test_conversion_passes_validated_options(app, client, uploads):
    (uploads / 'a.svg').write_text('<svg/>')
    r = client.post('/start_conversion', data={**CONVERT, 'linemerge': 'on'})
    assert r.status_code == 200 and r.data == b'Exported uploads/a.svg'
    args, _ = app.convert_stub.calls[0]
    assert args[:5] == ('uploads/a.svg', 'a4', 'portrait', 'hp7475a', '')
    assert args[-1] is app.main.socketio  # log messages must go through the app's socketio


@pytest.mark.parametrize('field,value', [
    ('outputsize', 'a9'), ('pageorientation', 'diagonal'), ('device', 'nope'), ('speed', '1; rm'),
])
def test_conversion_rejects_bad_options(client, uploads, field, value):
    (uploads / 'a.svg').write_text('<svg/>')
    assert client.post('/start_conversion', data={**CONVERT, field: value}).status_code == 400


@pytest.mark.parametrize('command', [
    'eval x', 'script foo', 'read /etc/passwd', 'write out.svg', 'forfile', 'include x', 'show',
    '--include x', 'linesort %__import__("os")%', 'a" eval "b', 'x; rm', 'a$(id)', 'a`id`', 'a\nb',
    'linesort\n',  # '$' accepts a trailing newline if the check uses match() instead of fullmatch()
])
def test_custom_command_is_restricted(client, uploads, command):
    (uploads / 'a.svg').write_text('<svg/>')
    assert client.post('/start_conversion', data={**CONVERT, 'command_input': command}).status_code == 400


def test_custom_command_allowed(client, uploads):
    (uploads / 'a.svg').write_text('<svg/>')
    r = client.post('/start_conversion', data={**CONVERT, 'command_input': 'linesort linemerge --tolerance 0.2mm'})
    assert r.status_code == 200


def test_conversion_failure_is_reported(app, client, uploads, monkeypatch):
    (uploads / 'a.svg').write_text('<svg/>')

    def explode(*args, **kwargs):
        raise SystemExit(1)  # vpype can exit instead of raising

    monkeypatch.setattr(app.main, 'convert_file', explode)
    assert client.post('/start_conversion', data=CONVERT).data == b'File not converted.'


# ---- plotting -------------------------------------------------------------------------------

def test_plot_rejects_bad_input(client, uploads):
    (uploads / 'a.hpgl').write_text('IN;')
    assert client.post('/start_plot', data={**PLOT, 'file': 'missing.hpgl'}).status_code == 400
    assert client.post('/start_plot', data={**PLOT, 'port': '/etc/passwd'}).status_code == 400
    assert client.post('/start_plot', data={**PLOT, 'port': '/dev/ttyAMA0\n'}).status_code == 400
    assert client.post('/start_plot', data={**PLOT, 'baudrate': 'fast'}).status_code == 400
    assert client.post('/start_plot', data={**PLOT, 'flowControl': 'magic'}).status_code == 400


def test_plot_lifecycle(app, client, uploads, slow_plot):
    (uploads / 'a.hpgl').write_text('IN;')
    assert client.post('/start_plot', data=PLOT).data == b'Plot started'
    assert wait_for(lambda: app.main.plot_lock.locked())

    assert client.post('/start_plot', data=PLOT).status_code == 409           # one plot at a time
    assert client.post('/delete_file', json={'filename': 'a.hpgl'}).status_code == 409
    upload = client.post('/', data={'file': (io.BytesIO(b'x'), 'a.hpgl')}, content_type='multipart/form-data')
    assert upload.status_code == 409                                           # not while it is plotting
    assert client.post('/update_baud', data={'selected_port': '/dev/ttyAMA0'}).status_code == 409

    assert client.post('/stop_plot').data == b'Plot stopped'
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert client.post('/stop_plot').data == b'No plot is running'


def test_failed_plot_releases_the_lock(app, client, uploads, monkeypatch):
    (uploads / 'a.hpgl').write_text('IN;')

    def explode(*args):
        raise RuntimeError('boom')

    monkeypatch.setattr(app.send2serial, 'sendToPlotter', explode)
    assert client.post('/start_plot', data=PLOT).data == b'Plot started'
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert client.post('/start_plot', data=PLOT).data == b'Plot started'  # not stuck


def test_socket_connect_syncs_lock_state(app, client, uploads, slow_plot):
    main = app.main

    def lock_state(sc):
        return [m['args'][0]['data'] for m in sc.get_received() if m['name'] == 'lock_edit']

    assert lock_state(main.socketio.test_client(main.app)) == ['off']
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: main.plot_lock.locked())
    assert lock_state(main.socketio.test_client(main.app)) == ['on']  # e.g. a refreshed page


def test_baud_detection_route(client):
    assert client.post('/update_baud', data={'selected_port': '/dev/ttyAMA0'}).status_code == 200
    assert client.post('/update_baud', data={'selected_port': '/etc/passwd'}).status_code == 400


def test_port_list(client):
    assert client.get('/update_ports').get_json()['content'] == ['/dev/ttyAMA0', '/dev/ttyUSB1']


# ---- configuration --------------------------------------------------------------------------

def test_config_round_trip(client):
    assert client.post('/save_configfile', data={'plotter_name': '50% Plotter', 'plotter_baudrate': '19200'}).status_code == 200
    saved = client.get('/save_configfile').get_json()
    assert saved['plotter_name'] == '50% Plotter'   # '%' must survive configparser interpolation
    assert saved['plotter_baudrate'] == '19200'


@pytest.mark.parametrize('field,value', [
    ('tasmota_ip', 'a b;c'), ('tasmota_enable', 'yes'), ('plotter_name', 'a\nb'),
    ('plotter_port', '/etc/passwd'), ('plotter_baudrate', 'x'), ('plotter_flowControl', 'x'),
    ('telegram_token', 'bad token'),
])
def test_config_rejects_invalid_values(client, field, value):
    before = client.get('/save_configfile').get_json()
    assert client.post('/save_configfile', data={field: value}).status_code == 400
    assert client.get('/save_configfile').get_json() == before


def test_config_save_is_applied_live(app, client):
    """notification/tasmota read the shared config object, so a UI save must not need a restart."""
    client.post('/save_configfile', data={'tasmota_enable': 'true', 'tasmota_ip': '10.1.2.3'})
    assert app.tasmota._enabled() and app.tasmota._ip() == '10.1.2.3'


# ---- tasmota --------------------------------------------------------------------------------

def test_tasmota_disabled_by_default(client):
    assert client.post('/action_tasmota').status_code == 409


def test_tasmota_unreachable_device_fails_fast(app, client, monkeypatch):
    client.post('/save_configfile', data={'tasmota_enable': 'true', 'tasmota_ip': '10.255.255.1'})
    seen = {}

    def get(url, **kwargs):
        seen.update(kwargs)
        raise requests.exceptions.Timeout()

    monkeypatch.setattr(app.tasmota.requests, 'get', get)
    assert client.post('/action_tasmota').status_code == 502
    assert seen.get('timeout')  # without a timeout an offline device would hang the request
