"""The buttons of the Pi Plot shield, with a fake gpiozero."""
import sys
import types

import pytest

from test_routes import PLOT, slow_plot, wait_for      # noqa: F401  (slow_plot is a fixture)


class FakeButton:
    made = []

    def __init__(self, pin, pull_up=True, bounce_time=None):
        if pin in FakeButton.broken:
            raise OSError('pin is in use')
        self.pin, self.pull_up, self.bounce_time = pin, pull_up, bounce_time
        self.when_pressed = None
        self.closed = False
        FakeButton.made.append(self)

    def close(self):
        self.closed = True

    def press(self):
        self.when_pressed()


FakeButton.broken = set()


@pytest.fixture
def gpio(monkeypatch):
    FakeButton.made = []
    FakeButton.broken = set()
    module = types.ModuleType('gpiozero')
    module.Button = FakeButton
    monkeypatch.setitem(sys.modules, 'gpiozero', module)
    return FakeButton


def configure(app, **values):
    if not app.main.config.has_section('buttons'):
        app.main.config.add_section('buttons')
    for key, value in values.items():
        app.main.config.set('buttons', key, value)


def pressed(app, name):
    [button] = [b for b in FakeButton.made if b.pin == app.buttons.PINS[name] and not b.closed]
    button.press()


def test_nothing_is_claimed_until_the_buttons_are_switched_on(app, gpio):
    assert app.main.setup_buttons() is None
    assert gpio.made == []


def test_the_shields_pins_are_used_as_its_documentation_says(app, gpio):
    configure(app, buttons_enable='true')
    assert app.main.setup_buttons() is None
    assert sorted((b.pin, b.pull_up) for b in gpio.made) == [(22, False), (27, False)]
    assert app.buttons.active() == 2


def test_applying_again_lets_go_of_the_old_buttons(app, gpio):
    configure(app, buttons_enable='true')
    app.main.setup_buttons()
    first = list(gpio.made)
    configure(app, button_stop_action='none')
    app.main.setup_buttons()
    assert all(b.closed for b in first) and app.buttons.active() == 1
    configure(app, buttons_enable='false')
    app.main.setup_buttons()
    assert app.buttons.active() == 0


def test_a_missing_library_or_pin_is_reported_not_raised(app, gpio, monkeypatch):
    configure(app, buttons_enable='true')
    gpio.broken = {22}
    assert 'GPIO 22' in app.main.setup_buttons()
    monkeypatch.setitem(sys.modules, 'gpiozero', None)
    assert 'gpiozero' in app.main.setup_buttons()


def test_the_stop_button_stops_a_plot(app, client, uploads, slow_plot, gpio):
    configure(app, buttons_enable='true')
    app.main.setup_buttons()
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: app.main.plot_lock.locked() and app.globals.printing)
    pressed(app, 'stop')
    assert app.globals.stop_requested
    assert wait_for(lambda: not app.main.plot_lock.locked())


def test_the_start_button_resumes_a_held_plot(app, client, uploads, slow_plot, gpio):
    configure(app, buttons_enable='true')
    app.main.setup_buttons()
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: app.main.plot_lock.locked())
    client.post('/pause_plot')
    assert app.globals.paused
    pressed(app, 'start')
    assert not app.globals.paused
    pressed(app, 'start')               # a plot that is running is left alone
    assert app.main.plot_lock.locked() and not app.globals.stop_requested


def test_the_start_button_starts_the_queue(app, client, uploads, monkeypatch, gpio):
    calls = []
    monkeypatch.setattr(app.send2serial, 'sendToPlotter', lambda *args, **kwargs: calls.append(args) or True)
    monkeypatch.setattr(app.main, 'wait_seconds', lambda *args, **kwargs: None)
    configure(app, buttons_enable='true')
    app.main.setup_buttons()
    told = []
    pressed(app, 'start')               # nothing queued
    (uploads / 'a.hpgl').write_text('IN;')
    assert client.post('/queue/add', data=dict(PLOT, file='a.hpgl')).status_code == 200
    pressed(app, 'start')
    assert wait_for(lambda: len(calls) == 1 and not app.globals.queue_active)


def test_the_pause_action_toggles(app, client, uploads, slow_plot, gpio):
    configure(app, buttons_enable='true', button_start_action='pause')
    app.main.setup_buttons()
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: app.main.plot_lock.locked())
    pressed(app, 'start')
    assert app.globals.paused
    pressed(app, 'start')
    assert not app.globals.paused


def test_a_press_is_told_to_the_log(app, gpio):
    said = []
    configure(app, buttons_enable='true')
    handlers = {'stop': lambda: 'No plot is running', 'start': lambda: (_ for _ in ()).throw(RuntimeError('x'))}
    assert app.buttons.press('stop', handlers, said.append) == 'The stop button: No plot is running'
    assert app.buttons.press('start', handlers, said.append) == 'The start button: failed (RuntimeError)'
    assert said == ['The stop button: No plot is running', 'The start button: failed (RuntimeError)']


@pytest.mark.parametrize('field,value', [('buttons_enable', 'yes'), ('button_start_action', 'reboot'), ('button_stop_action', '')])
def test_bad_button_settings_are_refused(client, field, value):
    assert client.post('/save_configfile', data={field: value}).status_code == 400


def test_saving_the_settings_sets_the_buttons_up(app, client, gpio):
    response = client.post('/save_configfile', data={'buttons_enable': 'true', 'button_start_action': 'pause'})
    assert response.status_code == 200 and app.buttons.active() == 2
    gpio.broken = {27}
    response = client.post('/save_configfile', data={'buttons_enable': 'true'})
    assert response.status_code == 200 and b'do not work' in response.data
    shown = client.get('/save_configfile').get_json()
    assert shown['buttons_enable'] == 'true' and shown['button_stop_action'] == 'stop'
