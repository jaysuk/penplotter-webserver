"""The read-only status for other programs."""
import base64

from test_routes import PLOT, slow_plot, wait_for      # noqa: F401  (slow_plot is a fixture)


def status(client, **kwargs):
    response = client.get('/api/status', **kwargs)
    assert response.status_code == 200
    return response.get_json()


def test_an_idle_plotter(app, client):
    data = status(client)
    assert data['state'] == 'idle' and data['plot']['running'] is False and data['last_plot'] is None
    assert data['queue'] == {'active': False, 'message': '', 'waiting': 0, 'items': []}
    assert data['plotter']
    assert 'log' not in data['plot']


def test_a_running_plot(app, client, uploads, slow_plot):
    (uploads / 'a.hpgl').write_bytes(b'IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: app.globals.printing and app.globals.plot_progress == 42)
    data = status(client)
    assert data['state'] == 'plotting' and data['plot']['file'] == 'a.hpgl' and data['plot']['progress'] == 42
    assert data['last_plot']['status'] == 'running'
    client.post('/pause_plot')
    assert status(client)['state'] == 'paused'
    app.globals.wait_reason = 'pen_change'
    assert status(client)['state'] == 'pen_change'
    client.post('/stop_plot')
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert status(client)['state'] == 'idle' and status(client)['last_plot']['status'] == 'stopped'


def test_the_queue_is_listed(app, client, uploads):
    (uploads / 'a.hpgl').write_bytes(b'IN;')
    (uploads / 'b.hpgl').write_bytes(b'IN;')
    client.post('/queue/add', data=dict(PLOT, file='a.hpgl'))
    client.post('/queue/add', data=dict(PLOT, file='b.hpgl'))
    assert status(client)['queue']['items'] == ['a.hpgl', 'b.hpgl'] and status(client)['queue']['waiting'] == 2
    app.globals.queue_active = True            # between two files: nothing holds the lock for a moment
    assert status(client)['state'] == 'plotting'


def test_status_needs_the_login_when_there_is_one(app, client):
    client.post('/save_configfile', data={'auth_username': 'admin', 'auth_password': 'pw'})
    assert client.get('/api/status').status_code == 401
    token = base64.b64encode(b'admin:pw').decode()
    assert status(client, headers={'Authorization': 'Basic ' + token})['state'] == 'idle'


def test_status_is_read_only(client):
    assert client.post('/api/status').status_code == 405
