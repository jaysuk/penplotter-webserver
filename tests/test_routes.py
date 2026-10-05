import base64
import io
import time
import types

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

    def fake_send(socketio, hpglfile, port, baud, flow, **kwargs):
        app.globals.printing = not app.globals.stop_requested
        socketio.emit('status_log', {'data': 'Configured for ' + flow})
        socketio.emit('buffer_size', {'data': '1024'})
        socketio.emit('print_progress', {'data': 42})
        socketio.emit('bytes_written', {'data': '42%, 420 bytes written.'})
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


def test_conversion_passes_page_options(app, client, uploads):
    (uploads / 'a.svg').write_text('<svg/>')
    r = client.post('/start_conversion', data={**CONVERT, 'margin': '12.5', 'rotate': '90',
                                               'mirror_x': 'on', 'mirror_y': 'on'})
    assert r.status_code == 200
    _, kwargs = app.convert_stub.calls[0]
    assert kwargs == {'margin': 12.5, 'rotate': 90, 'mirror_x': True, 'mirror_y': True, 'output': None}


def test_conversion_page_options_default_to_off(app, client, uploads):
    (uploads / 'a.svg').write_text('<svg/>')
    client.post('/start_conversion', data={**CONVERT, 'margin': ''})
    _, kwargs = app.convert_stub.calls[0]
    assert kwargs == {'margin': 0.0, 'rotate': 0, 'mirror_x': False, 'mirror_y': False, 'output': None}


@pytest.mark.parametrize('field,value', [
    ('margin', '-1'), ('margin', '51'), ('margin', 'x'), ('margin', '1e1'), ('margin', '5\n'), ('margin', '1.25'),
    ('margin', '١٢'), ('rotate', '45'), ('rotate', '-90'), ('rotate', '90.0'), ('rotate', 'x'),
])
def test_conversion_rejects_bad_page_options(client, uploads, field, value):
    (uploads / 'a.svg').write_text('<svg/>')
    assert client.post('/start_conversion', data={**CONVERT, field: value}).status_code == 400


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

    def explode(*args, **kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr(app.send2serial, 'sendToPlotter', explode)
    assert client.post('/start_plot', data=PLOT).data == b'Plot started'
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert client.post('/start_plot', data=PLOT).data == b'Plot started'  # not stuck
    assert wait_for(lambda: not app.main.plot_lock.locked())              # don't leak into the next test


def test_socket_connect_syncs_lock_state(app, client, uploads, slow_plot):
    main = app.main

    def lock_state(sc):
        return [m['args'][0]['data'] for m in sc.get_received() if m['name'] == 'lock_edit']

    assert lock_state(main.socketio.test_client(main.app)) == ['off']
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: app.globals.printing)       # under way, so its own broadcast is over
    assert lock_state(main.socketio.test_client(main.app)) == ['on']  # e.g. a refreshed page


def received(sc, name):
    return [m['args'][0]['data'] for m in sc.get_received() if m['name'] == name]


def test_socket_connect_restores_the_plot_view(app, client, uploads, slow_plot):
    """A refreshed page, or a second device, must see which file is plotting and how far it is."""
    main = app.main
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: main.plot_lock.locked() and app.globals.plot_progress == 42)

    [state] = received(main.socketio.test_client(main.app), 'plot_state')
    assert state['running'] is True and state['paused'] is False
    assert state['file'] == 'a.hpgl'
    assert state['progress'] == 42 and state['bytes_written'] == '42%, 420 bytes written.'
    assert state['buffer_size'] == '1024'
    assert state['log'] == [{'type': 'status_log', 'text': 'Configured for CTS/RTS'}]


def test_idle_connect_reports_no_plot(app):
    [state] = received(app.main.socketio.test_client(app.main.app), 'plot_state')
    assert state['running'] is False and state['file'] is None and state['buffer_size'] is None


def test_new_plot_forgets_the_previous_log(app, client, uploads, slow_plot):
    app.globals.record_event('status_log', {'data': 'left over'})
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: app.globals.plot_progress == 42)
    assert 'left over' not in [e['text'] for e in app.globals.plot_log_lines()]


def test_pause_and_resume(app, client, uploads, slow_plot):
    main = app.main
    assert client.post('/pause_plot').status_code == 409          # nothing to pause
    assert client.post('/resume_plot').status_code == 409

    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: main.plot_lock.locked())

    watcher = main.socketio.test_client(main.app)
    watcher.get_received()
    assert client.post('/pause_plot').data == b'Plot paused'
    assert app.globals.paused is True
    assert received(watcher, 'plot_state')[-1]['paused'] is True   # other clients are told
    assert received(main.socketio.test_client(main.app), 'plot_state')[0]['paused'] is True

    assert client.post('/resume_plot').data == b'Plot resumed'
    assert app.globals.paused is False
    assert received(watcher, 'plot_state')[-1]['paused'] is False


def test_stop_while_paused_ends_the_plot(app, client, uploads, slow_plot):
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: app.main.plot_lock.locked())
    client.post('/pause_plot')
    assert client.post('/stop_plot').data == b'Plot stopped'
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert app.globals.paused is False                               # does not carry into the next plot


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


def test_config_page_has_a_field_for_every_setting(app, client):
    """Every setting that can be saved must be editable in the UI."""
    page = client.get('/').get_data(as_text=True)
    assert [f for f in app.main.CONFIG_FIELDS if 'name="{}"'.format(f) not in page] == []


def basic(credentials):
    return {'Authorization': 'Basic ' + base64.b64encode(credentials.encode()).decode()}


def test_login_can_be_set_and_cleared_from_the_ui(app, client):
    assert client.post('/save_configfile', data={'auth_username': 'admin', 'auth_password': 'p%ss w0rd'}).status_code == 200
    assert client.get('/').status_code == 401
    assert client.get('/', headers=basic('admin:wrong')).status_code == 401
    assert client.get('/', headers=basic('admin:p%ss w0rd')).status_code == 200   # '%' survives saving

    # The password is never sent back, only whether one is set
    saved = client.get('/save_configfile', headers=basic('admin:p%ss w0rd')).get_json()
    assert saved['auth_username'] == 'admin' and saved['auth_password_set'] is True
    assert 'auth_password' not in saved and 'p%ss' not in str(saved)

    # An empty password field keeps the current password
    assert client.post('/save_configfile', headers=basic('admin:p%ss w0rd'),
                       data={'auth_username': 'boss', 'auth_password': ''}).status_code == 200
    assert client.get('/', headers=basic('boss:p%ss w0rd')).status_code == 200

    # Clearing the user name turns the login off
    assert client.post('/save_configfile', headers=basic('boss:p%ss w0rd'),
                       data={'auth_username': '', 'auth_password': ''}).status_code == 200
    assert client.get('/').status_code == 200
    assert client.get('/save_configfile').get_json()['auth_password_set'] is False


def test_login_needs_a_password_and_a_usable_name(client):
    assert client.post('/save_configfile', data={'auth_username': 'admin', 'auth_password': ''}).status_code == 400
    assert client.post('/save_configfile', data={'auth_username': 'a:b', 'auth_password': 'x'}).status_code == 400
    assert client.get('/').status_code == 200                           # nothing was half saved


def test_hand_edited_login_with_a_single_percent_still_works(app, client):
    main = app.main
    main.config.add_section('auth')
    main.config.set('auth', 'username', 'u')
    main.config._sections['auth']['password'] = 'a%b'   # as read from a file edited by hand
    assert client.get('/', headers=basic('u:a%b')).status_code == 200


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


# ---- reboot / poweroff ----------------------------------------------------------------------

@pytest.fixture
def fake_sudo(app, monkeypatch):
    """Record the power commands instead of running them; `allowed` is what `sudo -n -l` answers."""
    state = {'allowed': True, 'ran': []}

    def run(cmd, **kwargs):
        assert cmd[:3] == ['sudo', '-n', '-l']
        return type('R', (), {'returncode': 0 if state['allowed'] else 1})()

    monkeypatch.setattr(app.main.subprocess, 'run', run)
    monkeypatch.setattr(app.main.subprocess, 'Popen', lambda cmd, **kw: state['ran'].append(cmd))
    return state


@pytest.mark.parametrize('route,command', [('/action_reboot', 'reboot'), ('/action_poweroff', 'poweroff')])
def test_power_action_runs_with_sudo(client, fake_sudo, route, command):
    response = client.post(route)
    assert response.status_code == 200
    response.close()
    assert fake_sudo['ran'] == [['sudo', '-n', command]]


@pytest.mark.parametrize('route', ['/action_reboot', '/action_poweroff'])
def test_power_action_reports_missing_sudo_rights(client, fake_sudo, route):
    fake_sudo['allowed'] = False
    response = client.post(route)
    assert response.status_code == 500
    assert b'passwordless sudo' in response.data
    response.close()
    assert fake_sudo['ran'] == []


# ---- plot history ---------------------------------------------------------------------------

def history_rows(client):
    return client.get('/job_history').get_json()


def test_completed_plot_is_recorded(app, client, uploads, slow_plot):
    slow_plot['release'] = True
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: not app.main.plot_lock.locked() and history_rows(client))
    [job] = history_rows(client)
    assert job['file'] == 'a.hpgl' and job['status'] == 'completed'
    assert job['port'] == '/dev/ttyAMA0' and job['baudrate'] == 9600 and job['flow_control'] == 'CTS/RTS'
    assert job['finished_at'] >= job['started_at'] and job['error'] is None


def test_running_and_stopped_plots_are_recorded(app, client, uploads, slow_plot):
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: [j['status'] for j in history_rows(client)] == ['running'])
    client.post('/stop_plot')
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert [j['status'] for j in history_rows(client)] == ['stopped']


def test_failed_plot_keeps_the_reason(app, client, uploads, monkeypatch):
    (uploads / 'a.hpgl').write_text('IN;')

    def refuse(socketio, *args, **kwargs):
        socketio.emit('error', {'data': 'could not open port'})
        return False

    monkeypatch.setattr(app.send2serial, 'sendToPlotter', refuse)
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: not app.main.plot_lock.locked() and history_rows(client))
    [job] = history_rows(client)
    assert job['status'] == 'failed' and job['error'] == 'could not open port'


def test_crashed_plot_is_recorded_as_failed(app, client, uploads, monkeypatch):
    (uploads / 'a.hpgl').write_text('IN;')
    monkeypatch.setattr(app.send2serial, 'sendToPlotter', lambda *a, **k: 1 / 0)
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: not app.main.plot_lock.locked() and history_rows(client))
    job = history_rows(client)[0]
    assert job['status'] == 'failed' and 'ZeroDivisionError' in job['error']


def test_a_plot_cut_short_by_a_restart_is_marked_interrupted(app, client):
    app.history.start('a.hpgl', '/dev/x', 9600, 'CTS/RTS')
    app.history.init()          # what happens when the server starts again
    assert history_rows(client)[0]['status'] == 'interrupted'


def test_clear_history_keeps_the_running_plot(app, client):
    done = app.history.start('old.hpgl', '/dev/x', 9600, 'None')
    app.history.finish(done, 'completed', 100)
    app.history.start('now.hpgl', '/dev/x', 9600, 'None')
    assert client.post('/clear_history').status_code == 200
    assert [j['file'] for j in history_rows(client)] == ['now.hpgl']
    assert client.get('/clear_history').status_code == 405


def test_history_is_trimmed(app, client, monkeypatch):
    monkeypatch.setattr(app.history, 'MAX_ROWS', 3)
    for n in range(6):
        app.history.finish(app.history.start('f{}.hpgl'.format(n), '/dev/x', 9600, 'None'), 'completed')
    assert [j['file'] for j in history_rows(client)] == ['f5.hpgl', 'f4.hpgl', 'f3.hpgl']


def test_history_problems_never_stop_a_plot(app, client, uploads, slow_plot, monkeypatch):
    monkeypatch.setattr(app.history, 'DB_PATH', str(uploads.parent / 'missing_dir' / 'history.db'))
    slow_plot['release'] = True
    (uploads / 'a.hpgl').write_text('IN;')
    assert client.post('/start_plot', data=PLOT).data == b'Plot started'
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert client.get('/job_history').get_json() == []


# ---- tasmota power on / off -----------------------------------------------------------------

@pytest.fixture
def tasmota_calls(app, client, monkeypatch):
    """Enable tasmota, record the Power commands and the Telegram messages."""
    calls, messages = [], []

    def get(url, params=None, **kwargs):
        calls.append(params['cmnd'])
        return types.SimpleNamespace(content=b'{}')

    monkeypatch.setattr(app.tasmota.requests, 'get', get)
    monkeypatch.setattr(app.main.notification, 'telegram_sendNotification', messages.append)
    client.post('/save_configfile', data={'tasmota_enable': 'true', 'tasmota_ip': '10.1.2.3'})
    return types.SimpleNamespace(power=calls, telegram=messages)


def test_tasmota_switches_on_then_off_after_the_delay(app, client, uploads, slow_plot, tasmota_calls):
    client.post('/save_configfile', data={'tasmota_on_delay': '0', 'tasmota_off_delay': '1'})
    slow_plot['release'] = True
    (uploads / 'a.hpgl').write_text('IN;')
    start = time.time()
    client.post('/start_plot', data={**PLOT, 'tasmota': 'on'})
    assert wait_for(lambda: tasmota_calls.power == ['Power On', 'Power Off'], 5)
    assert time.time() - start >= 1            # waited for the plotter to finish drawing


def test_stop_skips_the_wait_before_power_off(app, client, uploads, slow_plot, tasmota_calls):
    client.post('/save_configfile', data={'tasmota_on_delay': '0', 'tasmota_off_delay': '120'})
    slow_plot['release'] = True
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data={**PLOT, 'tasmota': 'on'})
    assert wait_for(lambda: 'Waiting 120 s' in ' '.join(e['text'] for e in app.globals.plot_log_lines()))
    assert client.post('/stop_plot').data == b'Skipping the wait'
    assert wait_for(lambda: tasmota_calls.power == ['Power On', 'Power Off'] and not app.main.plot_lock.locked())
    assert history_rows(client)[0]['status'] == 'completed'     # the plot itself had finished
    assert not any('Cancelled' in m for m in tasmota_calls.telegram)


def test_stop_during_start_up_wait_cancels_the_plot(app, client, uploads, monkeypatch, tasmota_calls):
    sent = []
    monkeypatch.setattr(app.send2serial, 'sendToPlotter', lambda *a, **k: sent.append(a))
    client.post('/save_configfile', data={'tasmota_on_delay': '120'})
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data={**PLOT, 'tasmota': 'on'})
    assert wait_for(lambda: tasmota_calls.power == ['Power On'])
    client.post('/stop_plot')
    assert wait_for(lambda: not app.main.plot_lock.locked(), 5)
    assert sent == []                          # nothing was sent to the plotter
    assert history_rows(client)[0]['status'] == 'stopped'


def test_tasmota_delays_are_validated(client):
    saved = client.get('/save_configfile').get_json()
    assert saved['tasmota_on_delay'] == '2' and saved['tasmota_off_delay'] == '30'
    for value in ('601', '-1', 'x', '', '1.5'):
        assert client.post('/save_configfile', data={'tasmota_off_delay': value}).status_code == 400
    assert client.post('/save_configfile', data={'tasmota_off_delay': '45'}).status_code == 200
    assert client.get('/save_configfile').get_json()['tasmota_off_delay'] == '45'


# ---- stable serial port names ---------------------------------------------------------------

BY_ID = '/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A50285BI-if00-port0'


def test_usb_adapters_are_listed_by_their_stable_name(app, client, monkeypatch):
    monkeypatch.setattr(app.send2serial, 'byIdPorts', lambda: {BY_ID: '/dev/ttyUSB1'})
    # /dev/ttyUSB1 is the same adapter, so it is listed once; the Pi's own UART stays
    assert client.get('/update_ports').get_json()['content'] == [BY_ID, '/dev/ttyAMA0']


def test_by_id_names_are_read_from_the_system(app, tmp_path, monkeypatch):
    try:
        (tmp_path / 'usb-A-if00-port0').symlink_to('/dev/ttyUSB1')
    except OSError:
        pytest.skip('cannot create symlinks here')
    monkeypatch.setattr(app.send2serial, 'SERIAL_BY_ID', str(tmp_path))
    assert list(app.send2serial.byIdPorts()) == [str(tmp_path) + '/usb-A-if00-port0']


def test_no_by_id_directory_means_no_adapters(app, tmp_path, monkeypatch):
    monkeypatch.setattr(app.send2serial, 'SERIAL_BY_ID', str(tmp_path / 'missing'))
    assert app.send2serial.byIdPorts() == {}     # no adapters, or not Linux


def test_saved_default_port_stays_selectable(client):
    client.post('/save_configfile', data={'plotter_port': '/dev/ttyUSB9'})
    assert '/dev/ttyUSB9' in client.get('/update_ports').get_json()['content']


def test_plot_accepts_a_by_id_port(app, client, uploads, slow_plot):
    (uploads / 'a.hpgl').write_text('IN;')
    assert client.post('/start_plot', data={**PLOT, 'port': BY_ID}).data == b'Plot started'


def test_the_page_loads_the_theme(client):
    page = client.get('/').get_data(as_text=True)
    assert 'css/theme.css' in page and 'theme.js' in page and 'id="themeChoice"' in page
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert os.path.isfile(os.path.join(root, 'static', 'css', 'theme.css'))
    assert os.path.isfile(os.path.join(root, 'static', 'theme.js'))
