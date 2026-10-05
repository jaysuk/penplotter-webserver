"""Notifications: which events are sent, and where to."""
import sys
import time
import types

import pytest
import requests

from test_routes import PLOT, slow_plot, wait_for      # noqa: F401  (slow_plot is a fixture)
from test_send2serial import SMALL_PLOT

TELEGRAM = {'telegram_token': '123:abc', 'telegram_chatid': '42'}


@pytest.fixture
def sent(app, client, monkeypatch):
    """Everything the notification channels were asked to send."""
    log = types.SimpleNamespace(telegram=[], webhook=[], mqtt=[], webhook_status=200, webhook_error=None)

    monkeypatch.setattr(app.main.notification, 'telegram_sendNotification', lambda text: log.telegram.append(text) or b'ok')

    def post(url, json=None, timeout=None, **kwargs):
        if log.webhook_error:
            raise log.webhook_error
        log.webhook.append((url, json, timeout))
        return types.SimpleNamespace(status_code=log.webhook_status)

    monkeypatch.setattr(app.main.notification.requests, 'post', post)

    publish = types.ModuleType('paho.mqtt.publish')
    publish.single = lambda topic, payload, **kwargs: log.mqtt.append((topic, payload, kwargs))
    mqtt = types.ModuleType('paho.mqtt')
    mqtt.publish = publish
    paho = types.ModuleType('paho')
    paho.mqtt = mqtt
    monkeypatch.setitem(sys.modules, 'paho', paho)
    monkeypatch.setitem(sys.modules, 'paho.mqtt', mqtt)
    monkeypatch.setitem(sys.modules, 'paho.mqtt.publish', publish)
    return log


def save(client, **fields):
    response = client.post('/save_configfile', data=fields)
    assert response.status_code == 200, response.data
    return response


@pytest.fixture
def notification(app):
    return app.main.notification


def test_every_event_is_sent_by_default_but_progress(app, client, sent, notification):
    save(client, **TELEGRAM)
    for event in ('start', 'finish', 'error', 'attention'):
        assert notification.send(event, 'hello ' + event) is True
    assert sent.telegram == ['hello start', 'hello finish', 'hello error', 'hello attention']
    assert notification.send('progress', 'half way') is False          # off until a percentage is set
    assert notification.send('nonsense', 'x') is False


@pytest.mark.parametrize('field,event', [('notify_start', 'start'), ('notify_finish', 'finish'),
                                         ('notify_error', 'error'), ('notify_pen_change', 'attention')])
def test_events_can_be_switched_off(app, client, sent, notification, field, event):
    save(client, **TELEGRAM)
    save(client, **{field: 'false'})
    assert notification.send(event, 'x') is False and sent.telegram == []
    save(client, **{field: 'true'})
    assert notification.send(event, 'x') is True and sent.telegram == ['x']


def test_a_bad_hand_edited_value_falls_back_to_the_default(app, client, sent, notification):
    save(client, **TELEGRAM)
    app.main.config.set('notifications', 'notify_start', 'maybe')
    assert notification.send('start', 'x') is True
    app.main.config.set('notifications', 'notify_progress_every', 'lots')
    assert notification.progress_every() == 0


def test_a_message_goes_to_every_channel_that_is_set_up(app, client, sent, notification):
    save(client, **TELEGRAM, webhook_url='https://example.org/hook', mqtt_host='broker.local', mqtt_topic='plotters/one',
         mqtt_username='me', mqtt_password='p%ss')
    notification.send('finish', 'Done', file='a')

    assert sent.telegram == ['Done']
    [(url, payload, timeout)] = sent.webhook
    assert url == 'https://example.org/hook' and timeout <= 10
    assert payload['event'] == 'finish' and payload['message'] == 'Done' and payload['file'] == 'a'
    assert payload['plotter'] and isinstance(payload['time'], int)
    [(topic, body, kwargs)] = sent.mqtt
    assert topic == 'plotters/one/finish' and '"message": "Done"' in body
    assert kwargs['hostname'] == 'broker.local' and kwargs['port'] == 1883
    assert kwargs['auth'] == {'username': 'me', 'password': 'p%ss'}


def test_nothing_is_sent_without_a_channel(app, client, sent, notification):
    assert notification.channels() == []
    notification.send('start', 'x')
    assert sent.webhook == [] and sent.mqtt == []


def test_a_failing_channel_never_raises_and_never_blocks_the_others(app, client, sent, notification):
    save(client, **TELEGRAM, webhook_url='https://example.org/hook', mqtt_host='broker.local')
    sent.webhook_error = requests.exceptions.ConnectionError('secret details')
    app.main.notification.mqtt_settings()
    results = notification.deliver({'event': 'start', 'message': 'x'})
    assert results['Webhook'] == 'ConnectionError' and results['Telegram'] is None and results['MQTT'] is None
    sent.webhook_error = None
    sent.webhook_status = 500
    assert notification.deliver({'event': 'start', 'message': 'x'})['Webhook'] == 'HTTP 500'


def test_mqtt_without_the_library_is_reported_not_raised(app, client, sent, notification, monkeypatch):
    save(client, mqtt_host='broker.local')
    monkeypatch.setitem(sys.modules, 'paho.mqtt.publish', None)       # makes the import fail
    assert notification.deliver({'event': 'start', 'message': 'x'}) == {'MQTT': 'paho-mqtt is not installed'}


def test_mqtt_errors_are_reported_without_details(app, client, sent, notification, monkeypatch):
    save(client, mqtt_host='broker.local', mqtt_password='hunter2', mqtt_username='me')

    def refuse(*args, **kwargs):
        raise OSError('connection to me:hunter2@broker.local refused')

    monkeypatch.setattr(sys.modules['paho.mqtt.publish'], 'single', refuse)
    assert notification.deliver({'event': 'start', 'message': 'x'}) == {'MQTT': 'OSError'}


def test_delivery_happens_in_the_background_when_not_in_tests(app, client, sent, notification, monkeypatch):
    save(client, **TELEGRAM)
    monkeypatch.setattr(notification, 'SYNC', False)
    started = time.time()
    sent_at = []
    monkeypatch.setattr(notification, 'telegram_sendNotification', lambda text: (time.sleep(0.3), sent_at.append(text)))
    assert notification.send('start', 'slow one') is True
    assert time.time() - started < 0.25                      # the caller did not wait for the network
    assert wait_for(lambda: sent_at == ['slow one'])


# ---- settings -------------------------------------------------------------------------------

@pytest.mark.parametrize('field,value', [
    ('notify_start', 'yes'), ('notify_progress_every', '51'), ('notify_progress_every', '-1'), ('notify_progress_every', 'x'),
    ('webhook_url', 'ftp://example.org'), ('webhook_url', 'https://exa mple.org'),
    ('mqtt_host', 'a b'), ('mqtt_host', 'host;rm'), ('mqtt_port', '0'), ('mqtt_port', '70000'), ('mqtt_port', 'x'),
    ('mqtt_topic', 'a b'), ('mqtt_topic', '#'),
])
def test_bad_notification_settings_are_refused(client, field, value):
    assert client.post('/save_configfile', data={field: value}).status_code == 400


def test_the_mqtt_password_is_never_sent_back(app, client):
    save(client, mqtt_host='broker.local', mqtt_username='me', mqtt_password='s3cret')
    shown = client.get('/save_configfile').get_json()
    assert 'mqtt_password' not in shown and 's3cret' not in str(shown) and shown['mqtt_password_set'] is True
    save(client, mqtt_password='')                          # empty keeps it
    assert app.main.notification.mqtt_settings()['password'] == 's3cret'
    assert client.get('/save_configfile').get_json()['mqtt_host'] == 'broker.local'


def test_defaults_are_shown_for_an_old_config(client):
    shown = client.get('/save_configfile').get_json()
    assert shown['notify_start'] == 'true' and shown['notify_progress_every'] == '0' and shown['mqtt_port'] == '1883'


def test_a_bad_mqtt_port_or_topic_in_a_hand_edited_file_is_replaced(app, client, notification):
    app.main.config.set('notifications', 'mqtt_host', 'broker')
    app.main.config.set('notifications', 'mqtt_port', 'x')
    app.main.config.set('notifications', 'mqtt_topic', 'bad topic!')
    assert notification.mqtt_settings()['port'] == 1883 and notification.mqtt_settings()['topic'] == 'webplotter'


# ---- the test button ------------------------------------------------------------------------

def test_the_test_button(app, client, sent):
    assert client.post('/action_test_notification').status_code == 400          # nothing is set up
    save(client, **TELEGRAM, webhook_url='https://example.org/hook')
    response = client.post('/action_test_notification')
    assert response.status_code == 200 and b'Telegram: sent' in response.data and b'Webhook: sent' in response.data
    assert sent.telegram == ['Test message from the web plotter']
    sent.webhook_error = requests.exceptions.ConnectionError('x')
    response = client.post('/action_test_notification')
    assert response.status_code == 502 and b'Webhook: failed (ConnectionError)' in response.data
    assert client.get('/action_test_notification').status_code == 405


# ---- what a plot sends ----------------------------------------------------------------------

@pytest.fixture
def plot(app, uploads):
    class SIO:
        def emit(self, *args, **kwargs):
            pass

    def run(flow='XON/XOFF', content=SMALL_PLOT):
        (uploads / 'n.hpgl').write_bytes(content)
        return app.send2serial.sendToPlotter(SIO(), 'uploads/n.hpgl', '/dev/x', 9600, flow)
    return run


def test_a_plot_says_when_it_starts_and_finishes(app, client, sent, plot):
    save(client, **TELEGRAM, webhook_url='https://example.org/hook')
    assert plot() is True
    assert [m.split(': ')[-1] for m in sent.telegram][0] == 'Starting'
    assert 'Finished' in sent.telegram[-1]
    events = [payload['event'] for _, payload, _ in sent.webhook]
    assert events == ['start', 'finish'] and sent.webhook[-1][1]['file'] == 'n'


def test_progress_is_sent_every_so_many_percent(app, client, sent, plot):
    save(client, **TELEGRAM, notify_progress_every='25', notify_start='false', notify_finish='false')
    plot(content=SMALL_PLOT * 4)
    messages = [m for m in sent.telegram if 'done' in m]
    assert [m.split(': ')[-1].split('%')[0] for m in messages] == ['25', '50', '75']
    assert all(m.endswith('done') for m in messages)          # no estimate: not a made up time left


def test_progress_that_skips_a_step_is_not_lost(app, client, sent, plot):
    save(client, **TELEGRAM, notify_progress_every='10', notify_start='false', notify_finish='false')
    plot(content=b'IN;PU0,0;PD1,1;' * 12)                      # tiny file: percent jumps by several at a time
    assert 1 <= len(sent.telegram) <= 9 and len(set(sent.telegram)) == len(sent.telegram)


def test_a_failed_plot_sends_an_error_with_the_reason(app, client, uploads, sent, monkeypatch):
    save(client, **TELEGRAM)
    app.serial.Serial.fail_open = True
    (uploads / 'a.hpgl').write_text('IN;PU0,0;PD1,1;')
    assert client.post('/start_plot', data=PLOT).data == b'Plot started'
    assert wait_for(lambda: not app.main.plot_lock.locked())
    [message] = [m for m in sent.telegram if 'Failed' in m]
    assert 'a.hpgl' in message and 'could not open port' in message
    assert not any('Error initializing' in m for m in sent.telegram)        # reported once, by the plot


def test_errors_can_be_switched_off(app, client, uploads, sent):
    save(client, **TELEGRAM, notify_error='false')
    app.serial.Serial.fail_open = True
    (uploads / 'a.hpgl').write_text('IN;PU0,0;PD1,1;')
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert not any('Failed' in m for m in sent.telegram)


def test_stopping_is_a_finish_message(app, client, uploads, sent, slow_plot):
    save(client, **TELEGRAM)
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: app.globals.printing)
    client.post('/stop_plot')
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert any('Cancelled' in m for m in sent.telegram)
    save(client, notify_finish='false')
    slow_plot['release'] = False
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: app.globals.printing)
    sent.telegram.clear()
    client.post('/stop_plot')
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert not any('Cancelled' in m for m in sent.telegram)

