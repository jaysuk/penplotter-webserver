"""Plotter profiles, their storage, sharing and the serial line options they carry."""
import io
import json
import os
import zipfile

import pytest

from test_backup import download, make_zip, restore
from test_routes import PLOT, wait_for

LINE = {'bytesize': '7', 'parity': 'E', 'stopbits': '2', 'xonxoff': 'on', 'rtscts': 'off', 'dsrdtr': 'auto',
        'dtr': 'off', 'rts': 'on', 'timeout': '5', 'open_delay': '0.01'}


def form(name='My Plotter', **extra):
    return {'name': name, 'device': 'dxy', 'baudrate': '19200', 'flowControl': 'XON/XOFF', 'pen_change': 'pause',
            'port': '/dev/ttyUSB9', **LINE, **extra}


def export_file(*profiles):
    return json.dumps({'app': 'webplotter', 'type': 'plotters', 'format': 1, 'plotters': list(profiles)})


def entry(name='Shared', ident=None, **settings):
    result = {'name': name, 'notes': '', 'settings': dict({'device': 'hp7550', 'baudrate': '9600'}, **settings)}
    if ident:
        result['id'] = ident
    return result


def upload(client, text, name='plotters.json'):
    return client.post('/plotters/import', data={'plotters': (io.BytesIO(text.encode()), name)},
                       content_type='multipart/form-data')


# ---- the serial line options -----------------------------------------------------------------

def test_the_line_defaults_are_8n1_and_as_needed(app):
    line, error = app.plotters.clean_line({})
    assert error is None
    assert line == {'bytesize': '8', 'parity': 'N', 'stopbits': '1', 'xonxoff': 'auto', 'rtscts': 'auto',
                    'dsrdtr': 'auto', 'dtr': 'auto', 'rts': 'auto', 'timeout': '', 'open_delay': '0'}
    typed = app.plotters.line_options(line)
    assert typed['bytesize'] == 8 and typed['parity'] == 'N' and typed['stopbits'] == 1
    assert typed['xonxoff'] is None and typed['dtr'] is None and typed['timeout'] is None and typed['open_delay'] == 0


@pytest.mark.parametrize('key,value', [
    ('bytesize', '9'), ('bytesize', '08'), ('parity', 'X'), ('parity', 'n'), ('stopbits', '3'), ('stopbits', '1.25'),
    ('dtr', 'yes'), ('xonxoff', 'true'), ('timeout', '-1'), ('timeout', '0.01'), ('timeout', '61'), ('timeout', 'x'),
    ('timeout', '1e1'), ('open_delay', '11'), ('open_delay', '-1'), ('open_delay', '١'),
])
def test_invalid_line_options_are_refused(app, key, value):
    _, error = app.plotters.clean_line({key: value})
    assert error


def test_line_options_are_typed_for_the_serial_port(app):
    line, _ = app.plotters.clean_line(LINE)
    typed = app.plotters.line_options(line)
    assert typed == {'bytesize': 7, 'parity': 'E', 'stopbits': 2, 'xonxoff': True, 'rtscts': False, 'dsrdtr': None,
                     'dtr': False, 'rts': True, 'timeout': 5.0, 'open_delay': 0.01}
    assert app.plotters.line_options({'stopbits': '1.5'})['stopbits'] == 1.5
    assert app.plotters.line_options({'parity': 'bad'})['parity'] == 'N'     # never an invalid value


def test_the_port_is_opened_with_the_line_options(app):
    port = app.send2serial.open_port(app, '/dev/x', 9600, 'CTS/RTS', line=app.plotters.line_options(LINE))
    kwargs = app.serial.Serial.instances[-1].kwargs
    assert port is app.serial.Serial.instances[-1]
    assert (kwargs['bytesize'], kwargs['parity'], kwargs['stopbits'], kwargs['timeout']) == (7, 'E', 2, 5.0)
    assert kwargs['xonxoff'] is True and kwargs['rtscts'] is False and 'dsrdtr' not in kwargs
    assert (port.dtr, port.rts) == (False, True) and port.port == '/dev/x'


def test_dtr_and_rts_are_set_before_the_port_opens(app):
    """Some adapters and boards reset when DTR rises as the port opens."""
    seen = []
    Serial = app.serial.Serial
    original = Serial.open

    def open_(self):
        seen.append((self.dtr, self.rts))
        original(self)

    Serial.open = open_
    try:
        app.send2serial.open_port(app, '/dev/x', 9600, 'None', line={'dtr': False, 'rts': False})
    finally:
        Serial.open = original
    assert seen == [(False, False)]


def test_without_line_options_the_port_is_as_before(app):
    app.send2serial.open_port(app, '/dev/x', 9600, 'CTS/RTS')
    port = app.serial.Serial.instances[-1]
    assert port.kwargs == {'baudrate': 9600, 'bytesize': 8, 'parity': 'N', 'stopbits': 1, 'timeout': 2.0}
    assert port.dtr is None and port.rts is None


def test_the_flow_control_still_decides_what_is_left_as_needed(app):
    send2serial = app.send2serial
    assert send2serial.serial_settings(9600, 'XON/XOFF')[0]['xonxoff'] is True
    assert send2serial.serial_settings(4800, 'HP-IB')[0]['baudrate'] == 9600
    # off by the user wins over what the flow control asks for
    assert send2serial.serial_settings(9600, 'XON/XOFF', {'xonxoff': False})[0]['xonxoff'] is False
    # a CalComp plot asks the plotter and needs the short timeout
    assert send2serial.serial_settings(9600, 'CalComp', {'timeout': 5.0})[0]['timeout'] == 0.1


def test_the_pause_after_opening_comes_before_the_set_up(app):
    app.send2serial.open_port(app, '/dev/x', 9600, 'CTS/RTS', line={'open_delay': 0.01})
    assert app.serial.Serial.instances[-1].written[0] == b'IN;\033.R'


def test_a_port_that_cannot_open_is_reported(app):
    events = []
    app.serial.Serial.fail_open = True
    assert app.send2serial.open_port(types_emit(events), '/dev/x', 9600, 'None', line={'dtr': True}) is None
    assert events and events[0][0] == 'error'


def types_emit(events):
    class Sink:
        def emit(self, name, data=None):
            events.append((name, data))
    return Sink()


# ---- the line options in a plot ----------------------------------------------------------------

@pytest.fixture
def sent(app, uploads, monkeypatch):
    calls = []

    def fake_send(socketio, hpglfile, port, baud, flow, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(app.send2serial, 'sendToPlotter', fake_send)
    (uploads / 'a.hpgl').write_text('IN;')
    return calls


def test_a_plot_sends_with_the_line_options_of_the_form(app, client, sent):
    assert client.post('/start_plot', data=dict(PLOT, **LINE)).data == b'Plot started'
    assert wait_for(lambda: sent)
    assert sent[0]['line'] == app.plotters.line_options(LINE)
    wait_for(lambda: not app.main.plot_lock.locked())
    assert app.history.recent()[0]['options']['parity'] == 'E'          # kept, so plot again uses them


def test_a_plot_without_line_options_uses_the_defaults(app, client, sent):
    """The form of an older page, or a stored plot from before these options existed."""
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: sent)
    assert sent[0]['line']['bytesize'] == 8 and sent[0]['line']['parity'] == 'N' and sent[0]['line']['dtr'] is None


def test_plot_again_keeps_the_line_options(app, client, sent):
    client.post('/start_plot', data=dict(PLOT, **LINE))
    assert wait_for(lambda: sent)
    wait_for(lambda: not app.main.plot_lock.locked())
    job = app.history.recent()[0]
    assert client.post('/replot', data={'job': job['id']}).data == b'Plot started'
    assert wait_for(lambda: len(sent) == 2)
    assert sent[1]['line'] == sent[0]['line']


@pytest.mark.parametrize('key,value', [('parity', 'Q'), ('bytesize', '4'), ('timeout', '999'), ('dtr', 'high')])
def test_a_plot_with_invalid_line_options_is_refused(app, client, sent, key, value):
    response = client.post('/start_plot', data=dict(PLOT, **{key: value}))
    assert response.status_code == 400 and not sent
    assert not app.main.plot_lock.locked()


def test_the_queue_keeps_the_line_options(app, client, uploads):
    (uploads / 'a.hpgl').write_text('IN;')
    response = client.post('/queue/add', data=dict(PLOT, **LINE))
    assert response.status_code == 200, response.data
    [item] = app.queue.items()
    assert item['options']['parity'] == 'E' and item['options']['open_delay'] == '0.01'


def test_manual_control_and_baud_detection_use_the_line_options(app, client):
    data = dict(PLOT, **LINE)
    assert client.post('/plotter/pen_up', data=data).data == b'OK'
    kwargs = app.serial.Serial.instances[-1].kwargs
    assert kwargs['parity'] == 'E' and kwargs['bytesize'] == 7
    assert client.post('/plotter/pen_up', data=dict(data, parity='Q')).status_code == 400
    assert client.post('/update_baud', data={'selected_port': '/dev/ttyAMA0', 'parity': 'Q'}).status_code == 400
    assert client.post('/update_baud', data={'selected_port': '/dev/ttyAMA0', 'parity': 'E'}).status_code == 200
    assert app.serial.Serial.instances[-1].kwargs['parity'] == 'E'


# ---- the profiles ---------------------------------------------------------------------------

def test_built_in_plotters_are_listed_and_valid(app, client):
    listed = client.get('/plotters').get_json()
    assert [p['id'] for p in listed if p['source'] == 'builtin'] == ['hp7475a', 'hp7440a', 'hp7550']
    # the file a maintainer pastes into is checked like any other
    assert len(app.plotters.builtin()) == 3
    assert all(app.plotters.clean_profile(p)[1] is None for p in listed)


def test_a_saved_plotter_is_listed_and_kept_in_userdata(app, client):
    assert client.post('/plotters', data=form()).data == b'Saved plotter My Plotter'
    mine = [p for p in client.get('/plotters').get_json() if p['source'] == 'custom']
    assert len(mine) == 1 and mine[0]['id'] == 'my-plotter' and not mine[0]['replaces']
    assert mine[0]['settings']['device'] == 'dxy' and mine[0]['settings']['parity'] == 'E'
    assert 'port' not in mine[0]['settings']                     # the port belongs to the machine, not the plotter
    assert os.path.isfile(os.path.join('userdata', 'plotters.json'))


def test_saving_under_the_same_name_replaces_it(app, client):
    client.post('/plotters', data=form())
    client.post('/plotters', data=form('my plotter', baudrate='4800'))
    mine = app.plotters.custom()
    assert len(mine) == 1 and mine[0]['settings']['baudrate'] == '4800'


@pytest.mark.parametrize('change', [
    {'name': ''}, {'name': '<b>x</b>'}, {'name': 'x' * 61}, {'device': 'laser'}, {'baudrate': '12'},
    {'flowControl': 'magic'}, {'pen_change': 'x'}, {'parity': 'Z'}, {'notes': 'a' * 301},
])
def test_invalid_plotters_are_refused(app, client, change):
    assert client.post('/plotters', data=form(**change)).status_code == 400
    assert not os.path.exists(os.path.join('userdata', 'plotters.json'))


def test_a_plotter_of_the_user_replaces_a_built_in_one_until_it_is_deleted(app, client):
    client.post('/plotters', data=form('HP 7475A', baudrate='4800'))
    listed = {p['id']: p for p in client.get('/plotters').get_json()}
    assert listed['hp-7475a']['source'] == 'custom'            # a different id: it is simply another plotter
    client.post('/plotters', data=form('hp7475a', baudrate='4800'))
    listed = {p['id']: p for p in client.get('/plotters').get_json()}
    assert listed['hp7475a']['source'] == 'custom' and listed['hp7475a']['replaces']
    assert listed['hp7475a']['settings']['baudrate'] == '4800'
    assert client.post('/plotters/delete', data={'id': 'hp7475a'}).status_code == 200
    listed = {p['id']: p for p in client.get('/plotters').get_json()}
    assert listed['hp7475a']['source'] == 'builtin' and listed['hp7475a']['settings']['baudrate'] == '9600'


def test_only_the_users_plotters_can_be_deleted(app, client):
    assert client.post('/plotters/delete', data={'id': 'hp7475a'}).status_code == 404
    assert client.post('/plotters/delete', data={'id': 'nothing'}).status_code == 404
    client.post('/plotters', data=form())
    assert client.post('/plotters/delete', data={'id': 'my-plotter'}).status_code == 200
    assert app.plotters.custom() == []


def test_plotters_survive_a_new_import_of_the_module(app, client):
    """They are in a file, not in memory."""
    client.post('/plotters', data=form())
    assert [p['name'] for p in app.plotters.custom()] == ['My Plotter']
    with open(os.path.join('userdata', 'plotters.json')) as f:
        assert json.load(f)['plotters'][0]['id'] == 'my-plotter'


def test_a_damaged_file_is_left_alone(app, client):
    os.makedirs('userdata', exist_ok=True)
    with open(os.path.join('userdata', 'plotters.json'), 'w') as f:
        f.write('{ this is not json')
    assert client.get('/plotters').status_code == 200              # the page still works
    assert client.post('/plotters', data=form()).status_code == 400
    with open(os.path.join('userdata', 'plotters.json')) as f:
        assert f.read() == '{ this is not json'


def test_slugs(app):
    assert app.plotters.slug('My Plotter (A3)') == 'my-plotter-a3'
    assert app.plotters.slug('!!!') == 'plotter'
    assert app.plotters.ID_RE.fullmatch(app.plotters.slug('x' * 100))


# ---- sharing ----------------------------------------------------------------------------------

def test_export_and_import_round_trip(app, client):
    client.post('/plotters', data=form('One'))
    client.post('/plotters', data=form('Two', device='hp7550'))
    response = client.get('/plotters/export')
    assert response.status_code == 200
    assert 'attachment' in response.headers['Content-Disposition'] and '.json' in response.headers['Content-Disposition']
    document = json.loads(response.data)
    assert document['app'] == 'webplotter' and document['type'] == 'plotters'
    assert [p['name'] for p in document['plotters']] == ['One', 'Two']
    assert all('port' not in p['settings'] for p in document['plotters'])

    for name in ('one', 'two'):
        client.post('/plotters/delete', data={'id': name})
    result = upload(client, response.data.decode())
    assert result.get_json() == {'imported': 2, 'names': ['One', 'Two']}
    assert [p['name'] for p in app.plotters.custom()] == ['One', 'Two']


def test_one_plotter_can_be_exported_even_a_built_in_one(app, client):
    document = json.loads(client.get('/plotters/export?id=hp7550').data)
    assert [p['id'] for p in document['plotters']] == ['hp7550']
    assert client.get('/plotters/export?id=nothing').status_code == 404
    assert client.get('/plotters/export').status_code == 404        # none of the user's own yet


def test_an_export_can_be_pasted_into_the_built_in_file(app, client):
    """What the maintainer does with a file that was sent in."""
    client.post('/plotters', data=form('Sent In'))
    sent_in = client.get('/plotters/export').data.decode()
    assert app.plotters.parse_export(sent_in)[0]['settings']['parity'] == 'E'


def test_import_adds_to_what_is_there(app, client):
    client.post('/plotters', data=form('Mine'))
    assert upload(client, export_file(entry('Shared'))).status_code == 200
    assert [p['name'] for p in app.plotters.custom()] == ['Mine', 'Shared']
    assert app.plotters.get('shared')['settings']['baudrate'] == '9600'


@pytest.mark.parametrize('text', [
    'not json', '[]', '{}', json.dumps({'app': 'other', 'type': 'plotters', 'format': 1, 'plotters': []}),
    json.dumps({'app': 'webplotter', 'type': 'plotters', 'format': 2, 'plotters': []}),
    json.dumps({'app': 'webplotter', 'type': 'plotters', 'format': 1, 'plotters': 'x'}),
    export_file(entry('Good'), entry('Bad', device='laser')),           # one bad plotter refuses the file
    export_file(entry('Bad', parity='Z')),
    export_file({'name': 'x', 'settings': 'x'}),
    export_file(entry('A', ident='same'), entry('B', ident='same')),
    export_file(entry('A', ident='../../etc/passwd')),
    export_file('a string'),
])
def test_a_bad_file_imports_nothing(app, client, text):
    assert upload(client, text).status_code == 400
    assert app.plotters.custom() == []


def test_import_needs_a_file_and_a_size_limit(app, client):
    assert client.post('/plotters/import').status_code == 400
    assert upload(client, ' ' * (app.plotters.MAX_FILE_BYTES + 10)).status_code == 400
    assert upload(client, export_file()).status_code == 400          # nothing in it


def test_there_is_a_limit_on_plotters(app, client, monkeypatch):
    monkeypatch.setattr(app.plotters, 'MAX_PROFILES', 2)
    client.post('/plotters', data=form('A'))
    client.post('/plotters', data=form('B'))
    assert client.post('/plotters', data=form('C')).status_code == 400
    assert client.post('/plotters', data=form('A', baudrate='4800')).status_code == 200      # replacing is fine


def test_plotter_routes_refuse_cross_site_requests(client):
    response = client.post('/plotters', data=form(), headers={'Origin': 'http://evil.example'})
    assert response.status_code == 403


# ---- the default plotter ---------------------------------------------------------------------

def test_the_default_plotter_is_a_setting(app, client):
    assert client.post('/save_configfile', data={'plotter_profile': 'my-plotter'}).status_code == 200
    assert client.get('/save_configfile').get_json()['plotter_profile'] == 'my-plotter'
    assert client.post('/save_configfile', data={'plotter_profile': ''}).status_code == 200
    for bad in ('My Plotter', '../x', 'a' * 41, '-x'):
        assert client.post('/save_configfile', data={'plotter_profile': bad}).status_code == 400


def test_the_flow_controls_the_profiles_know_are_the_ones_the_sender_knows(app):
    assert app.main.FLOW_CONTROLS == app.plotters.FLOW_CONTROLS
    assert app.send2serial.CAL_POLL in app.plotters.FLOW_CONTROLS
    assert app.plotters.DEVICES == app.main.DEVICES


def test_the_page_has_the_plotter_and_serial_line_controls(client):
    page = client.get('/').get_data(as_text=True)
    for key in ('bytesize', 'parity', 'stopbits', 'xonxoff', 'rtscts', 'dsrdtr', 'dtr', 'rts', 'timeout', 'open_delay'):
        assert 'name="{}"'.format(key) in page
    assert 'id="plotterProfile"' in page and 'name="plotter_profile"' in page


# ---- backup ---------------------------------------------------------------------------------

def test_the_backup_holds_the_plotters_and_restoring_replaces_them(app, client):
    client.post('/plotters', data=form('Kept'))
    archive, _ = download(client)
    assert 'plotters.json' in archive.namelist()
    assert 'plotters' in json.loads(archive.read('manifest.json'))['contains']

    client.post('/plotters', data=form('Added Later'))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as out:
        for name in archive.namelist():
            out.writestr(name, archive.read(name))
    buffer.seek(0)
    response = restore(client, buffer)
    assert response.status_code == 200 and response.get_json()['plotters'] == 1
    assert [p['name'] for p in app.plotters.custom()] == ['Kept']


def test_a_backup_without_plotters_keeps_the_current_ones(app, client):
    client.post('/plotters', data=form('Mine'))
    response = restore(client, make_zip({}))
    assert response.status_code == 200 and response.get_json()['plotters'] == 0
    assert [p['name'] for p in app.plotters.custom()] == ['Mine']


def test_a_backup_with_bad_plotters_restores_nothing(app, client):
    client.post('/save_configfile', data={'plotter_name': 'Before'})
    archive = make_zip({'config.ini': '[plotter]\nname = After\n', 'plotters.json': export_file(entry('Bad', device='laser'))})
    response = restore(client, archive)
    assert response.status_code == 400 and b'plotters' in response.data
    assert client.get('/save_configfile').get_json()['plotter_name'] == 'Before'
    assert app.plotters.custom() == []
