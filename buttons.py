"""The two buttons of the Pi Plot shield.

The shield wires them, through de-bouncing circuits, to GPIO 27 ("Start") and GPIO 22 ("Stop"): the
inputs are pulled down and a press is a rising edge (the shield's own example). What each does is
set in [buttons]; they are off until `buttons_enable` is true, so nothing claims the pins on a
computer without the shield. Uses gpiozero, which is imported only when the buttons are on.
"""
import threading

# Shared, live configuration object (updated when settings are saved in the UI)
from config import config

PINS = {'start': 27, 'stop': 22}        # BCM numbers, as the shield's documentation gives them
ACTIONS = ('none', 'start', 'stop', 'pause')
DEFAULT_ACTIONS = {'start': 'start', 'stop': 'stop'}
BOUNCE_S = 0.1

_buttons = []
_lock = threading.Lock()


def enabled():
    return config.get('buttons', 'buttons_enable', fallback='false', raw=True).strip().lower() == 'true'


def action_for(button):
    """What a button does: none, start (resume a held plot, else start the queue), stop or pause (toggle)."""
    value = config.get('buttons', 'button_{}_action'.format(button), fallback='', raw=True).strip()
    return value if value in ACTIONS else DEFAULT_ACTIONS[button]


def press(button, handlers, say):
    """A button was pressed. `handlers` maps an action to a function that does it and returns a message."""
    action = action_for(button)
    handler = handlers.get(action)
    if handler is None:
        return None
    try:
        message = handler()
    except Exception as e:          # a button must never take the server down
        message = 'failed ({})'.format(type(e).__name__)
    message = 'The {} button: {}'.format(button, message)
    say(message)
    return message


def apply(handlers, say=print):
    """(Re)claim the pins to match the settings. Returns None, or why the buttons are not working."""
    with _lock:
        for old in _buttons:
            try:
                old.close()
            except Exception:
                pass
        _buttons.clear()
        if not enabled():
            return None
        try:
            from gpiozero import Button
        except ImportError:
            return 'gpiozero is not installed (pip install gpiozero)'
        for name, pin in PINS.items():
            if action_for(name) == 'none':
                continue
            try:
                button = Button(pin, pull_up=False, bounce_time=BOUNCE_S)
            except Exception as e:      # no GPIO here, no permission, or the pin is in use
                return 'Could not use GPIO {} ({})'.format(pin, type(e).__name__)
            button.when_pressed = lambda name=name: press(name, handlers, say)
            _buttons.append(button)
        return None


def active():
    """Number of buttons that are set up."""
    with _lock:
        return len(_buttons)
