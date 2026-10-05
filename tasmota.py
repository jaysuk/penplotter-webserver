import requests

# Shared, live configuration object (updated when settings are saved in the UI)
from config import config

REQUEST_TIMEOUT = 5  # seconds


def _enabled():
    return config.get('tasmota', 'tasmota_enable', fallback='false').strip().lower() == 'true'


def _ip():
    return config.get('tasmota', 'tasmota_ip', fallback='').strip()


def _delay(option, default):
    """Seconds from config.ini, kept within 0..600."""
    try:
        return max(0, min(600, int(config.get('tasmota', option, fallback=str(default)).strip())))
    except ValueError:
        return default


def on_delay():
    """Seconds to let the plotter start up after it is switched on, before sending."""
    return _delay('tasmota_on_delay', 2)


def off_delay():
    """Seconds to let the plotter finish drawing before it is switched off."""
    return _delay('tasmota_off_delay', 30)


def _send_command(socketio, command):
    """Send a Power command to the Tasmota device. Returns the response body, or False on failure."""
    ip = _ip()
    if not ip:
        socketio.emit('error', {'data': 'No Tasmota IP address configured'})
        return False
    try:
        response = requests.get(
            "http://{ip}/cm".format(ip=ip),
            params={'cmnd': 'Power {}'.format(command)},
            timeout=REQUEST_TIMEOUT
        )
        return response.content
    except requests.exceptions.Timeout:
        message = 'Timeout while trying to contact Tasmota device'
    except requests.exceptions.TooManyRedirects:
        message = 'Too many redirects while trying to contact Tasmota device'
    except requests.exceptions.ConnectionError:
        message = 'Connection error while trying to contact Tasmota device'
    except requests.exceptions.RequestException as e:
        message = 'Tasmota request failed: {}'.format(repr(e))
    print(message)
    socketio.emit('error', {'data': message})
    return False


def tasmota_setStatus(socketio, status):
    if not _enabled():
        return False
    if status not in ('on', 'off'):
        print('Please use only on or off')
        return False
    return _send_command(socketio, status.capitalize())


def tasmota_setToggle(socketio):
    if not _enabled():
        return False
    return _send_command(socketio, 'TOGGLE')
