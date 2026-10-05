"""The plot queue: several files plotted in a row."""
import pytest

from test_routes import PLOT, slow_plot, wait_for      # noqa: F401  (slow_plot is a fixture)


def history_rows(client):
    return client.get('/job_history').get_json()


@pytest.fixture
def quick(app, uploads, monkeypatch):
    """Plots that finish at once: records what was sent, and the Tasmota power commands."""
    state = {'sent': [], 'power': [], 'result': True}

    def fake_send(socketio, hpglfile, port, baud, flow, **kwargs):
        state['sent'].append(hpglfile.replace('\\', '/').split('/')[-1])
        socketio.emit('print_progress', {'data': 100})
        return state['result']

    monkeypatch.setattr(app.send2serial, 'sendToPlotter', fake_send)
    monkeypatch.setattr(app.main, 'wait_seconds', lambda *args, **kwargs: None)
    monkeypatch.setattr(app.tasmota, 'tasmota_setStatus', lambda events, status: state['power'].append(status))
    app.main.config.set('tasmota', 'tasmota_enable', 'true')       # the power commands are only used with Tasmota on
    for name in ('a.hpgl', 'b.hpgl', 'c.hpgl'):
        (uploads / name).write_text('IN;')
    return state


def add(client, name='a.hpgl', **extra):
    return client.post('/queue/add', data=dict(PLOT, file=name, **extra))


def queue(client):
    return client.get('/queue').get_json()


def names(client):
    return [item['file'] for item in queue(client)['items']]


def idle(app):
    return wait_for(lambda: not app.globals.queue_active and not app.main.plot_lock.locked())


def test_the_queue_is_edited_through_its_routes(app, client, quick):
    for name in ('a.hpgl', 'b.hpgl', 'c.hpgl'):
        assert add(client, name).data == b'Added to the queue'
    assert names(client) == ['a.hpgl', 'b.hpgl', 'c.hpgl']

    ids = [item['id'] for item in queue(client)['items']]
    assert client.post('/queue/move', data={'id': ids[2], 'direction': 'up'}).status_code == 200
    assert names(client) == ['a.hpgl', 'c.hpgl', 'b.hpgl']
    assert client.post('/queue/move', data={'id': ids[0], 'direction': 'up'}).status_code == 409   # already first
    assert client.post('/queue/move', data={'id': ids[0], 'direction': 'sideways'}).status_code == 400

    assert client.post('/queue/pause_after', data={'id': ids[0], 'value': '1'}).status_code == 200
    assert queue(client)['items'][0]['pause_after'] is True
    assert client.post('/queue/pause_after', data={'id': 999, 'value': '1'}).status_code == 404

    assert client.post('/queue/remove', data={'id': ids[1]}).status_code == 200
    assert client.post('/queue/remove', data={'id': ids[1]}).status_code == 404
    assert names(client) == ['a.hpgl', 'c.hpgl']
    assert client.post('/queue/clear').status_code == 200
    assert names(client) == []
    for route in ('/queue/add', '/queue/remove', '/queue/move', '/queue/clear', '/queue/start'):
        assert client.get(route).status_code == 405


def test_the_queue_checks_what_is_added(app, client, quick):
    assert add(client, 'missing.hpgl').status_code == 400
    assert add(client, 'a.hpgl', port='nonsense').status_code == 400
    assert add(client, 'a.hpgl', flowControl='bogus').status_code == 400
    assert names(client) == []
    assert client.post('/queue/start').status_code == 400          # empty


def test_the_queue_is_limited(app, client, quick, monkeypatch):
    monkeypatch.setattr(app.queue, 'MAX_ITEMS', 2)
    assert add(client).status_code == 200 and add(client).status_code == 200
    assert add(client).status_code == 400


def test_the_queue_plots_every_file_in_order(app, client, quick):
    for name in ('a.hpgl', 'c.hpgl', 'b.hpgl'):
        add(client, name, tasmota='on')
    assert client.post('/queue/start').data == b'Queue started'
    assert idle(app)

    assert quick['sent'] == ['a.hpgl', 'c.hpgl', 'b.hpgl']
    assert names(client) == []
    assert queue(client)['message'] == 'The queue is finished'
    assert [job['file'] for job in reversed(history_rows(client))] == ['a.hpgl', 'c.hpgl', 'b.hpgl']
    assert all(job['status'] == 'completed' for job in history_rows(client))
    # The plotter is switched on once, before the first file, and off once, after the last
    assert quick['power'] == ['on', 'off']


def test_nothing_else_can_start_while_the_queue_runs(app, client, quick, slow_plot):
    add(client, 'a.hpgl')
    add(client, 'b.hpgl')
    client.post('/queue/start')
    assert wait_for(lambda: app.globals.printing)      # the plot is under way

    assert client.post('/queue/start').status_code == 409
    assert client.post('/start_plot', data=PLOT).status_code == 409
    assert client.post('/delete_file', json={'filename': 'b.hpgl'}).status_code == 409
    running = queue(client)['items'][0]
    assert running['status'] == 'running'
    assert client.post('/queue/remove', data={'id': running['id']}).status_code == 404
    assert app.main.plot_state()['queue_active'] is True

    client.post('/stop_plot')
    assert idle(app)


def test_a_file_in_the_queue_cannot_be_deleted(app, client, quick):
    add(client, 'a.hpgl')
    assert client.post('/delete_file', json={'filename': 'a.hpgl'}).status_code == 409
    client.post('/queue/clear')
    assert client.post('/delete_file', json={'filename': 'a.hpgl'}).status_code == 200


def test_stop_holds_the_queue_instead_of_clearing_it(app, client, quick, slow_plot):
    for name in ('a.hpgl', 'b.hpgl', 'c.hpgl'):
        add(client, name, tasmota='on')
    client.post('/queue/start')
    assert wait_for(lambda: app.globals.printing)      # the plot is under way

    assert client.post('/stop_plot').data == b'Plot stopped'
    assert idle(app)
    items = queue(client)['items']
    assert [item['file'] for item in items] == ['a.hpgl', 'b.hpgl', 'c.hpgl']      # nothing was lost
    assert all(item['status'] == 'waiting' for item in items)
    assert queue(client)['message'] == 'The queue is stopped'
    assert history_rows(client)[0]['status'] == 'stopped'
    assert quick['power'] == ['on', 'off']              # a held queue does not leave the plotter on

    # Start again: the stopped file is plotted again, then the rest
    slow_plot['release'] = True
    client.post('/queue/start')
    assert idle(app)
    assert names(client) == []


def test_a_failed_plot_holds_the_queue(app, client, quick):
    quick['result'] = False
    add(client, 'a.hpgl')
    add(client, 'b.hpgl')
    client.post('/queue/start')
    assert idle(app)

    assert quick['sent'] == ['a.hpgl']
    assert names(client) == ['a.hpgl', 'b.hpgl']
    assert 'a.hpgl failed' in queue(client)['message']


def test_a_file_that_went_away_holds_the_queue(app, client, quick, uploads):
    add(client, 'a.hpgl')
    add(client, 'b.hpgl')
    (uploads / 'a.hpgl').unlink()
    client.post('/queue/start')
    assert idle(app)
    assert quick['sent'] == [] and names(client) == ['a.hpgl', 'b.hpgl']
    assert 'a.hpgl' in queue(client)['message']


def test_pause_for_a_paper_change_between_plots(app, client, quick):
    add(client, 'a.hpgl', tasmota='on', pause_after='1')
    add(client, 'b.hpgl', tasmota='on')
    client.post('/queue/start')
    assert wait_for(lambda: app.globals.paused and app.globals.wait_reason == 'paper_change')

    state = app.main.plot_state()
    assert state['paused'] is True and state['wait_reason'] == 'paper_change'
    assert quick['sent'] == ['a.hpgl'] and quick['power'] == ['on']        # the plotter stays on

    assert client.post('/resume_plot').status_code == 200
    assert idle(app)
    assert quick['sent'] == ['a.hpgl', 'b.hpgl'] and quick['power'] == ['on', 'off']


def test_a_paper_change_after_the_last_plot_is_not_waited_for(app, client, quick):
    add(client, 'a.hpgl', pause_after='1')
    client.post('/queue/start')
    assert idle(app)
    assert quick['sent'] == ['a.hpgl']


def test_stop_during_a_paper_change_holds_the_queue(app, client, quick):
    add(client, 'a.hpgl', pause_after='1')
    add(client, 'b.hpgl')
    client.post('/queue/start')
    assert wait_for(lambda: app.globals.wait_reason == 'paper_change')

    assert client.post('/stop_plot').status_code == 200
    assert idle(app)
    assert quick['sent'] == ['a.hpgl']
    assert names(client) == ['b.hpgl']                  # the first one was done and left the queue
    assert history_rows(client)[0]['status'] == 'completed'


def test_the_queue_is_sent_to_a_connecting_page(app, client, quick):
    add(client, 'a.hpgl')
    sc = app.main.socketio.test_client(app.main.app)
    received = [m['args'][0]['data'] for m in sc.get_received() if m['name'] == 'queue_state']
    assert received[-1]['items'][0]['file'] == 'a.hpgl' and received[-1]['active'] is False


def test_a_restart_puts_a_running_plot_back_in_the_queue(app, client, quick):
    add(client, 'a.hpgl')
    [item] = app.queue.items()
    app.queue.set_status(item['id'], 'running')
    app.queue.init()                                    # what the server does when it starts
    assert app.queue.items()[0]['status'] == 'waiting'
