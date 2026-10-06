"""Test setup.

The app modules read config.ini and uploads/ relative to the working directory, and main.py
changes into its own directory on import. To keep the repository clean the sources are copied
into a temporary directory and imported from there, with a fake serial port and a stub
converter (the real converter is tested separately in test_convert.py and needs vpype).
"""
import os
import shutil
import sys
import types

import pytest

import fake_serial

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = ['main.py', 'send2serial.py', 'config.py', 'notification.py', 'tasmota.py', 'globals.py', 'history.py', 'backup.py', 'plot_queue.py', 'hpgl_analysis.py', 'plotter_control.py', 'presets.py', 'text_drawing.py', 'vpype_plugins.py', 'plotters.py', 'vpype_devices.py', 'timelapse.py', 'buttons.py', 'ui_state.py', 'updater.py', 'changelog.py']
APP_MODULES = ['main', 'send2serial', 'config', 'notification', 'tasmota', 'globals', 'history', 'backup', 'plot_queue', 'hpgl_analysis', 'plotter_control', 'presets', 'text_drawing', 'vpype_plugins', 'plotters', 'vpype_devices', 'timelapse', 'buttons', 'ui_state', 'updater', 'changelog', 'convert_vpype']


@pytest.fixture(scope='session')
def env(tmp_path_factory):
    """Import the app once, from a temporary copy, and return its modules."""
    work = tmp_path_factory.mktemp('app')
    for name in SOURCES + ['plotters_builtin.json', 'VERSION', 'CHANGELOG.md']:
        shutil.copy(os.path.join(ROOT, name), work)
    shutil.copytree(os.path.join(ROOT, 'templates'), work / 'templates')
    (work / 'uploads').mkdir()

    saved_cwd = os.getcwd()
    saved_modules = {name: sys.modules.get(name) for name in list(fake_serial.make_modules()) + APP_MODULES}
    os.chdir(work)
    sys.path.insert(0, str(work))
    sys.modules.update(fake_serial.make_modules())

    # Stub converter: records the call and returns what the real one would on success
    stub = types.ModuleType('convert_vpype')
    stub.calls = []

    def convert_file(*args, **kwargs):
        stub.calls.append((args, kwargs))
        if kwargs.get('output') and not stub.fail:
            with open(kwargs['output'], 'wb') as f:
                f.write(stub.hpgl)
        return 'Exported ' + (kwargs.get('output') or args[0])

    def output_name(file, *args, **kwargs):
        return os.path.splitext(file)[0] + '-converted.hpgl'

    def create_text(*args, **kwargs):
        stub.text_calls.append((args, kwargs))
        if stub.text_error:
            raise stub.text_error
        with open(kwargs['output'], 'w') as f:
            f.write('<svg/>')
        return kwargs['output']

    stub.text_calls = []
    stub.text_error = None
    stub.create_text = create_text
    stub.fail = False
    stub.hpgl = b'IN;SP1;PU400,800;PD2400,800,2400,2800;PU;SP2;PU0,0;PD400,400;PU;SP0;'
    stub.convert_file = convert_file
    stub.output_name = output_name
    sys.modules['convert_vpype'] = stub

    import main
    import send2serial
    import notification
    import tasmota
    import globals as app_globals
    import history
    import plot_queue
    import hpgl_analysis
    import plotter_control
    import presets
    import plotters
    import vpype_devices
    import timelapse
    import buttons
    import updater
    import changelog

    notification.telegram_sendNotification = lambda message: False
    notification.SYNC = True        # deliver in the test's own thread

    yield types.SimpleNamespace(
        dir=work, main=main, send2serial=send2serial, tasmota=tasmota,
        globals=app_globals, history=history, queue=plot_queue, hpgl=hpgl_analysis, control=plotter_control, presets=presets, plotters=plotters, devices=vpype_devices, timelapse=timelapse, buttons=buttons, updater=updater, changelog=changelog, convert_stub=stub, serial=fake_serial)

    os.chdir(saved_cwd)
    sys.path.remove(str(work))
    for name, module in saved_modules.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


@pytest.fixture
def app(env):
    """The app with clean uploads, config and serial state for each test."""
    main = env.main
    uploads = env.dir / 'uploads'
    for entry in uploads.iterdir():
        entry.unlink()
    shutil.rmtree(env.dir / 'userdata', ignore_errors=True)
    shutil.rmtree(env.dir / 'timelapse', ignore_errors=True)
    env.timelapse._active.clear()
    env.timelapse._rendering.clear()
    env.convert_stub.calls.clear()
    env.convert_stub.fail = False
    env.convert_stub.text_calls.clear()
    env.convert_stub.text_error = None
    env.serial.reset()
    env.globals.initialize()
    env.history.DB_PATH = 'history.db'
    env.history.init()
    with env.history._connect() as conn:
        conn.execute('DELETE FROM jobs')
    env.presets.init()
    env.queue.init()
    with env.history._connect() as conn:
        conn.execute('DELETE FROM presets')
        conn.execute('DELETE FROM queue_items')

    snapshot = {section: dict(main.config[section]) for section in main.config.sections()}
    yield env
    for section in list(main.config.sections()):
        main.config.remove_section(section)
    for section, values in snapshot.items():
        main.config.add_section(section)
        for key, value in values.items():
            main.config.set(section, key, value.replace('%', '%%'))


@pytest.fixture
def client(app):
    return app.main.app.test_client()


@pytest.fixture
def uploads(app):
    return app.dir / 'uploads'
