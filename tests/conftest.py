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
SOURCES = ['main.py', 'send2serial.py', 'config.py', 'notification.py', 'tasmota.py', 'globals.py']
APP_MODULES = ['main', 'send2serial', 'config', 'notification', 'tasmota', 'globals', 'convert_vpype']


@pytest.fixture(scope='session')
def env(tmp_path_factory):
    """Import the app once, from a temporary copy, and return its modules."""
    work = tmp_path_factory.mktemp('app')
    for name in SOURCES:
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
        return 'Exported ' + args[0]

    stub.convert_file = convert_file
    sys.modules['convert_vpype'] = stub

    import main
    import send2serial
    import notification
    import tasmota
    import globals as app_globals

    notification.telegram_sendNotification = lambda message: False

    yield types.SimpleNamespace(
        dir=work, main=main, send2serial=send2serial, tasmota=tasmota,
        globals=app_globals, convert_stub=stub, serial=fake_serial)

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
    env.convert_stub.calls.clear()
    env.serial.reset()
    env.globals.initialize()

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
