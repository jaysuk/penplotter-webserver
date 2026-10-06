"""Messages about what the plotter is doing, sent to Telegram, a webhook and MQTT.

Call `send(event, message, **data)`. Which events are sent is set in the [notifications] section of
config.ini (read at call time, so a save from the UI applies at once). Delivery happens in a
background thread with short timeouts: a slow network must never hold up the plot.
"""
import configparser
import html
import json
import queue
import re
import threading
import time

import requests

# Shared, live configuration object (updated when settings are saved in the UI)
from config import config

REQUEST_TIMEOUT = 5  # seconds
MQTT_TIMEOUT = 10

# Tests deliver in the calling thread, so they can look at the result straight away
SYNC = False

# The events, and the setting that switches each on or off. 'attention' is anything that needs
# someone at the plotter: a pen or paper change, the plotter being back after a lost connection.
TOGGLES = {
    'start': 'notify_start',
    'finish': 'notify_finish',      # finished, and stopped
    'error': 'notify_error',        # failed, or the connection was lost
    'attention': 'notify_pen_change',
    'progress': 'notify_progress_every',
    'update': 'notify_update',      # a newer version of the web plotter is out
}
EVENTS = set(TOGGLES) | {'test'}

HOST_RE = re.compile(r'[A-Za-z0-9.-]+')
TOPIC_RE = re.compile(r'[A-Za-z0-9_/-]{1,100}')
URL_RE = re.compile(r'https?://[^\s]+')


def _setting(option):
    """Return a telegram setting, or '' if unset or still the sample placeholder."""
    value = config.get('telegram', option, fallback='').strip()
    if not value or re.fullmatch(r'X+', value):
        return ''
    return value


def _value(section, option, default=''):
    """A config value as the UI wrote it ('%' doubled), or as it is when a hand edited file has a
    single '%' that configparser refuses to interpolate."""
    try:
        return config.get(section, option, fallback=default)
    except configparser.InterpolationError:
        return config.get(section, option, raw=True, fallback=default)


def _notifications(option, default=''):
    return _value('notifications', option, default).strip()


def _flag(option, default=True):
    value = _notifications(option).lower()
    if value in ('true', '1', 'yes', 'on'):
        return True
    if value in ('false', '0', 'no', 'off'):
        return False
    return default


def progress_every():
    """Send a message every this many percent (0 = never)."""
    try:
        return min(max(int(_notifications('notify_progress_every', '0') or 0), 0), 50)
    except ValueError:
        return 0


def enabled(event):
    if event == 'test':
        return True
    if event == 'progress':
        return progress_every() > 0
    return event in TOGGLES and _flag(TOGGLES[event])


def webhook_url():
    url = _notifications('webhook_url')
    return url if URL_RE.fullmatch(url) else ''


def mqtt_settings():
    """The MQTT broker settings, or None when MQTT is not set up."""
    host = _notifications('mqtt_host')
    if not HOST_RE.fullmatch(host):
        return None
    try:
        port = int(_notifications('mqtt_port', '1883') or 1883)
    except ValueError:
        port = 1883
    topic = _notifications('mqtt_topic', 'webplotter') or 'webplotter'
    return {'host': host, 'port': port if 0 < port < 65536 else 1883,
            'topic': topic.strip('/') if TOPIC_RE.fullmatch(topic) else 'webplotter',
            'username': _notifications('mqtt_username'), 'password': _notifications('mqtt_password')}


def channels():
    """Names of the places messages are sent to."""
    found = []
    if _setting('telegram_token') and _setting('telegram_chatid'):
        found.append('Telegram')
    if webhook_url():
        found.append('Webhook')
    if mqtt_settings():
        found.append('MQTT')
    return found


def telegram_sendNotification(notification):
    token = _setting('telegram_token')
    chat_id = _setting('telegram_chatid')
    if not (token and chat_id):
        return False

    payload = {
        'chat_id': chat_id,
        'text': html.escape(str(notification), quote=False),
        'parse_mode': 'HTML'
    }
    try:
        return requests.post(
            "https://api.telegram.org/bot{token}/sendMessage".format(token=token),
            data=payload,
            timeout=REQUEST_TIMEOUT
        ).content
    except requests.exceptions.RequestException as e:
        print('Telegram notification failed: {}'.format(type(e).__name__))
        return False


def _webhook(payload):
    """Returns None when sent, otherwise why not."""
    url = webhook_url()
    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as e:
        return type(e).__name__
    status = getattr(response, 'status_code', 200)
    return None if status < 400 else 'HTTP {}'.format(status)


def _mqtt(payload):
    """Returns None when sent, otherwise why not."""
    settings = mqtt_settings()
    try:
        import paho.mqtt.publish as publish
    except ImportError:
        return 'paho-mqtt is not installed'
    auth = {'username': settings['username'], 'password': settings['password']} if settings['username'] else None
    try:
        publish.single('{}/{}'.format(settings['topic'], payload['event']), json.dumps(payload),
                       hostname=settings['host'], port=settings['port'], auth=auth, keepalive=MQTT_TIMEOUT)
    except Exception as e:      # paho raises OSError, ValueError and its own errors
        return type(e).__name__
    return None


def deliver(payload):
    """Send a message to every channel. Returns {channel: None when sent, or the reason it was not}."""
    results = {}
    text = payload['message']
    if _setting('telegram_token') and _setting('telegram_chatid'):
        results['Telegram'] = None if telegram_sendNotification(text) is not False else 'not sent'
    if webhook_url():
        results['Webhook'] = _webhook(payload)
    if mqtt_settings():
        results['MQTT'] = _mqtt(payload)
    for channel, reason in results.items():
        if reason:
            print('{} notification failed: {}'.format(channel, reason))
    return results


_jobs = queue.Queue()
_worker = None
_worker_lock = threading.Lock()


def _run():
    while True:
        payload = _jobs.get()
        try:
            deliver(payload)
        except Exception as e:      # nothing here may kill the worker
            print('Notification failed:', repr(e))


def send(event, message, **data):
    """Tell people about `event` (one of EVENTS) if it is switched on. `data` is added to the
    payload the webhook and MQTT receive (file, progress, ...). Returns whether it was queued."""
    global _worker
    if event not in EVENTS or not enabled(event):
        return False
    payload = dict(data, event=event, message=str(message), time=int(time.time()),
                   plotter=_value('plotter', 'name', 'Plotter'))
    if SYNC:
        deliver(payload)
        return True
    with _worker_lock:
        if _worker is None:
            _worker = threading.Thread(target=_run, daemon=True)
            _worker.start()
    _jobs.put(payload)
    return True


def send_test():
    """A test message to every channel, in the calling thread. Returns {channel: reason or None}."""
    return deliver({'event': 'test', 'message': 'Test message from the web plotter', 'time': int(time.time()),
                    'plotter': _value('plotter', 'name', 'Plotter')})
