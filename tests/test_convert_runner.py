"""Converting in a child process: the protocol between the server and the child, timeouts and cancelling,
and how the routes use it. The child here is a small script that stands in for convert_vpype, so no
vpype is needed (the real one is tried at the end when vpype is installed)."""
import io
import json
import os
import sys
import threading
import time

import pytest

from test_routes import CONVERT, wait_for


@pytest.fixture
def runner(app):
    return app.convert_runner


@pytest.fixture
def child(app, tmp_path):
    """A child command whose functions are the given source (they get `emit` through socketio)."""
    def make(functions_source):
        script = tmp_path / 'child.py'
        script.write_text(
            'import sys, time, os\n'
            'sys.path.insert(0, {!r})\n'
            'import convert_runner\n'
            'class TextError(Exception):\n    pass\n'
            '{}\n'
            'sys.exit(convert_runner.child_main(sys.stdin, sys.stdout, FUNCTIONS))\n'.format(str(app.dir), functions_source))
        return [sys.executable, str(script)]
    return make


def test_the_result_and_the_progress_come_back(app, runner, child):
    command = child('''
def convert_file(*args, socketio=None, **kwargs):
    socketio.emit('status_log', {'data': 'working on ' + args[0]})
    socketio.emit('status_log', {'data': 'File converted.'})
    return 'Exported ' + kwargs['output']
FUNCTIONS = {'convert_file': convert_file}
''')
    seen = []
    result = runner.run('convert_file', ['uploads/a.svg', 'a4'], {'output': 'x.hpgl'},
                        emit=lambda name, data: seen.append((name, data)), command=command)
    assert result == 'Exported x.hpgl'
    assert seen == [('status_log', {'data': 'working on uploads/a.svg'}), ('status_log', {'data': 'File converted.'})]
    assert not runner.running()


def test_what_the_child_prints_does_not_confuse_it(app, runner, child, capsys):
    command = child('''
def convert_file(*args, socketio=None, **kwargs):
    print('Converting file: ' + args[0])
    print('@@webplotter not json')
    print('{"result": "forged"}')
    return 'real'
FUNCTIONS = {'convert_file': convert_file}
''')
    assert runner.run('convert_file', ['a.svg'], {}, command=command) == 'real'
    assert 'Converting file: a.svg' in capsys.readouterr().out


def test_an_error_of_the_child_is_raised_here(app, runner, child):
    command = child('''
def create_text(*args, **kwargs):
    raise TextError('The text does not fit')
def convert_file(*args, **kwargs):
    raise SystemExit(2)
FUNCTIONS = {'create_text': create_text, 'convert_file': convert_file}
''')
    with pytest.raises(app.text.TextError, match='does not fit'):
        runner.run('create_text', ['hi'], {}, command=command, error_types={'TextError': app.text.TextError})
    with pytest.raises(runner.ConversionError):                  # an exception nobody asked for
        runner.run('create_text', ['hi'], {}, command=command)
    with pytest.raises(runner.ConversionError):                  # vpype ends with SystemExit
        runner.run('convert_file', ['a.svg'], {}, command=command)


def test_a_child_that_dies_is_reported(app, runner, child):
    command = child('''
def convert_file(*args, **kwargs):
    os._exit(7)
FUNCTIONS = {'convert_file': convert_file}
''')
    with pytest.raises(runner.ConversionError, match='exit code 7'):
        runner.run('convert_file', ['a.svg'], {}, command=command)
    assert not runner.running()


def test_a_child_that_cannot_start_is_reported(app, runner):
    with pytest.raises(runner.ConversionError, match='Could not start'):
        runner.run('convert_file', ['a.svg'], {}, command=['/no/such/program'])


def test_a_slow_conversion_is_stopped(app, runner, child):
    command = child('''
def convert_file(*args, **kwargs):
    time.sleep(60)
FUNCTIONS = {'convert_file': convert_file}
''')
    started = time.time()
    with pytest.raises(runner.ConversionError, match='took longer'):
        runner.run('convert_file', ['a.svg'], {}, command=command, timeout=1)
    assert time.time() - started < 10 and not runner.running()


def test_a_conversion_can_be_cancelled(app, runner, child):
    command = child('''
def convert_file(*args, socketio=None, **kwargs):
    socketio.emit('status_log', {'data': 'started'})
    time.sleep(60)
FUNCTIONS = {'convert_file': convert_file}
''')
    outcome, seen = [], []

    def work():
        try:
            outcome.append(runner.run('convert_file', ['a.svg'], {}, emit=lambda n, d: seen.append(d), command=command))
        except runner.ConversionError as e:
            outcome.append(e)

    assert runner.cancel() is False                              # nothing to cancel
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    assert wait_for(lambda: seen and runner.running(), timeout=10)
    assert runner.cancel() is True
    thread.join(10)
    assert not thread.is_alive() and 'cancelled' in str(outcome[0]) and not runner.running()


def test_only_the_known_functions_run(app, runner):
    with pytest.raises(ValueError):
        runner.run('os_system', [], {})
    out = io.StringIO()
    assert runner.child_main(io.StringIO(json.dumps({'function': 'os_system', 'args': []})), out, {}) == 1
    assert json.loads(out.getvalue()[len(runner.PREFIX):])['kind'] == 'ValueError'


def test_the_child_works_in_the_app_folder(app, runner, child, tmp_path):
    command = child('''
def convert_file(*args, **kwargs):
    return os.getcwd()
FUNCTIONS = {'convert_file': convert_file}
''')
    assert os.path.samefile(runner.run('convert_file', ['a.svg'], {}, command=command), runner.HERE)
    assert os.path.samefile(runner.run('convert_file', ['a.svg'], {}, command=command, cwd=str(tmp_path)), tmp_path)


@pytest.mark.skipif(os.name == 'nt', reason='nice is not on Windows')
def test_the_child_starts_at_a_low_priority(app, runner):
    import shutil
    if shutil.which('nice') is None:
        pytest.skip('no nice')
    command = runner._low_priority(['python', 'x'])
    assert command[1:3] == ['-n', '10'] and command[-2:] == ['python', 'x']


# ---- the routes --------------------------------------------------------------------------------

@pytest.fixture
def separate(app, monkeypatch):
    app.main.config.set('plotter', 'convert_separate', 'true')
    calls = []

    def fake_run(function, args, kwargs, emit=None, **extra):
        calls.append((function, args, kwargs, extra))
        json.dumps([args, kwargs])                               # what goes to the child must be plain JSON
        if function == 'create_text':
            if app.convert_stub.text_error:
                raise app.convert_stub.text_error
            return kwargs['output']
        return 'Exported ' + (kwargs['output'] or 'uploads/a-converted.hpgl')

    monkeypatch.setattr(app.convert_runner, 'run', fake_run)
    return calls


def test_a_conversion_goes_to_the_child(app, client, uploads, separate):
    (uploads / 'a.svg').write_text('<svg/>')
    response = client.post('/start_conversion', data=dict(CONVERT, margin='5', rotate='90', mirror_x='on'))
    assert response.status_code == 200 and response.data.startswith(b'Exported')
    function, args, kwargs, _ = separate[0]
    assert function == 'convert_file' and args[:4] == ['uploads/a.svg', 'a4', 'portrait', 'hp7475a']
    assert kwargs == {'margin': 5.0, 'rotate': 90, 'mirror_x': True, 'mirror_y': False, 'output': None}
    assert app.convert_stub.calls == []                          # not in this process


def test_a_preview_goes_to_the_child_too(app, client, uploads, separate, monkeypatch):
    (uploads / 'a.svg').write_text('<svg/>')

    def fake_run(function, args, kwargs, emit=None, **extra):
        with open(kwargs['output'], 'wb') as f:
            f.write(app.convert_stub.hpgl)
        return 'Exported'

    monkeypatch.setattr(app.convert_runner, 'run', fake_run)
    assert client.post('/preview_conversion', data=CONVERT).status_code == 200


def test_progress_from_the_child_reaches_the_page(app, client, uploads, separate, monkeypatch):
    (uploads / 'a.svg').write_text('<svg/>')

    def fake_run(function, args, kwargs, emit=None, **extra):
        emit('status_log', {'data': 'File converted.'})
        return 'Exported x'

    monkeypatch.setattr(app.convert_runner, 'run', fake_run)
    page = app.main.socketio.test_client(app.main.app)
    page.get_received()
    client.post('/start_conversion', data=CONVERT)
    assert {'data': 'File converted.'} in [m['args'][0] for m in page.get_received() if m['name'] == 'status_log']


def test_a_failed_child_is_reported_like_a_failed_conversion(app, client, uploads, monkeypatch):
    (uploads / 'a.svg').write_text('<svg/>')
    app.main.config.set('plotter', 'convert_separate', 'true')

    def fail(*args, **kwargs):
        raise app.convert_runner.ConversionError('The conversion took longer than 30 minutes and was stopped')

    monkeypatch.setattr(app.convert_runner, 'run', fail)
    page = app.main.socketio.test_client(app.main.app)
    page.get_received()
    assert client.post('/start_conversion', data=CONVERT).data == b'File not converted.'
    errors = [m['args'][0]['data'] for m in page.get_received() if m['name'] == 'error']
    assert errors and 'took longer' in errors[0]
    assert not app.main.conversion_lock.locked()                 # the next one may run


def test_a_text_drawing_goes_to_the_child_and_a_text_error_is_a_400(app, client, separate):
    data = {'text': 'Hello', 'font': 'futural', 'align': 'left', 'outputsize': 'a4', 'pageorientation': 'portrait',
            'size': '20', 'margin': '15'}
    assert client.post('/create_text', data=data).status_code == 200
    assert separate[0][0] == 'create_text'
    app.convert_stub.text_error = app.text.TextError('The text does not fit')
    response = client.post('/create_text', data=data)
    assert response.status_code == 400 and b'does not fit' in response.data


def test_the_setting_turns_it_off(app, client, uploads):
    (uploads / 'a.svg').write_text('<svg/>')
    app.main.config.remove_option('plotter', 'convert_separate')      # as in an older config.ini
    assert app.main.separate_conversion() is True                # on unless it is turned off
    app.main.config.set('plotter', 'convert_separate', 'false')
    assert app.main.separate_conversion() is False
    client.post('/start_conversion', data=CONVERT)
    assert len(app.convert_stub.calls) == 1                      # in this process again


def test_the_setting_is_saved_from_the_page(app, client):
    app.main.config.remove_option('plotter', 'convert_separate')
    assert client.get('/save_configfile').get_json()['convert_separate'] == 'true'
    assert client.post('/save_configfile', data={'convert_separate': 'false'}).status_code == 200
    assert client.get('/save_configfile').get_json()['convert_separate'] == 'false'
    assert client.post('/save_configfile', data={'convert_separate': 'maybe'}).status_code == 400


def test_cancel_route(app, client, monkeypatch):
    assert client.post('/conversion/cancel').status_code == 404
    monkeypatch.setattr(app.convert_runner, 'cancel', lambda: True)
    assert client.post('/conversion/cancel').status_code == 200
    assert client.get('/conversion/cancel').status_code == 405


# ---- the real converter -------------------------------------------------------------------------

def test_the_real_converter_runs_in_a_child(app, tmp_path):
    pytest.importorskip('vpype')
    pytest.importorskip('vpype_cli')
    from test_convert import SVG, ROOT
    # The child must be the real one from the repository (the copy under test has only the stub converter next to it)
    command = [sys.executable, os.path.join(ROOT, 'convert_runner.py')]
    (tmp_path / 'uploads').mkdir()
    (tmp_path / 'uploads' / 'a.svg').write_text(SVG)
    seen = []
    result = app.convert_runner.run(
        'convert_file', ['uploads/a.svg', 'a4', 'portrait', 'hp7475a', '', '', '', '', '', ''], {},
        emit=lambda name, data: seen.append(data['data']), command=command, cwd=str(tmp_path), timeout=300)
    assert result.startswith('Exported') and 'File converted.' in seen
    assert any(name.endswith('.hpgl') for name in os.listdir(tmp_path / 'uploads'))
