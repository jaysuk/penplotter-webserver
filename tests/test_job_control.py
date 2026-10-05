"""Plotting again from the history, the queue, resuming and reconnecting."""
import time

import pytest

from test_routes import PLOT, slow_plot, wait_for      # noqa: F401  (slow_plot is a fixture)


def history_rows(client):
    return client.get('/job_history').get_json()


def start_and_finish(app, client, slow_plot, **extra):
    assert client.post('/start_plot', data=dict(PLOT, **extra)).data == b'Plot started'
    assert wait_for(lambda: app.main.plot_lock.locked())
    slow_plot['release'] = True
    assert wait_for(lambda: not app.main.plot_lock.locked())
    slow_plot['release'] = False


# ---- plot again -------------------------------------------------------------------------------

def test_the_settings_of_a_plot_are_kept(app, client, uploads, slow_plot, monkeypatch):
    monkeypatch.setattr(app.main, 'wait_seconds', lambda *args, **kwargs: None)
    monkeypatch.setattr(app.tasmota, 'tasmota_setStatus', lambda *args, **kwargs: None)
    (uploads / 'a.hpgl').write_text('IN;')
    start_and_finish(app, client, slow_plot, tasmota='on', pen_change='pause')

    [job] = app.history.recent()
    assert job['options'] == {'file': 'a.hpgl', 'port': '/dev/ttyAMA0', 'baudrate': '9600',
                              'flowControl': 'CTS/RTS', 'tasmota': 'on', 'timelapse': '',
                              'pens': '', 'pen_change': 'pause'}
    assert job['file_size'] == 3
    assert 'options' not in history_rows(client)[0]        # the page does not need them


def test_plot_again_uses_the_same_settings(app, client, uploads, monkeypatch):
    calls = []

    def fake_send(socketio, hpglfile, port, baud, flow, **kwargs):
        calls.append((hpglfile, port, baud, flow, kwargs['pen_pause']))
        return True

    monkeypatch.setattr(app.send2serial, 'sendToPlotter', fake_send)
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=dict(PLOT, pen_change='pause'))
    assert wait_for(lambda: len(calls) == 1 and not app.main.plot_lock.locked())

    [job] = history_rows(client)
    assert job['can_replot'] is True
    assert client.post('/replot', data={'job': job['id']}).data == b'Plot started'
    assert wait_for(lambda: len(calls) == 2 and not app.main.plot_lock.locked())
    assert calls[1] == calls[0] and calls[1][4] is True
    assert len(history_rows(client)) == 2


def test_plot_again_works_for_plots_recorded_before_settings_were_kept(app, client, uploads, monkeypatch):
    seen = []
    monkeypatch.setattr(app.send2serial, 'sendToPlotter',
                        lambda socketio, path, port, baud, flow, **kw: seen.append((port, baud, flow)) or True)
    (uploads / 'a.hpgl').write_text('IN;')
    job = app.history.start('a.hpgl', '/dev/ttyAMA0', 19200, 'XON/XOFF')
    app.history.finish(job, 'completed', 100)

    assert client.post('/replot', data={'job': job}).data == b'Plot started'
    assert wait_for(lambda: seen and not app.main.plot_lock.locked())
    assert seen == [('/dev/ttyAMA0', 19200, 'XON/XOFF')]


def test_plot_again_refuses_what_cannot_be_plotted(app, client, uploads, slow_plot):
    job = app.history.start('gone.hpgl', '/dev/ttyAMA0', 9600, 'None')
    app.history.finish(job, 'completed', 100)
    assert history_rows(client)[0]['can_replot'] is False      # the file was deleted
    assert client.post('/replot', data={'job': job}).status_code == 400
    assert client.post('/replot', data={'job': 999}).status_code == 404
    assert client.post('/replot', data={'job': 'x'}).status_code == 404
    assert client.get('/replot').status_code == 405

    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: app.main.plot_lock.locked())
    assert client.post('/replot', data={'job': job}).status_code in (400, 409)
    done = app.history.start('a.hpgl', '/dev/ttyAMA0', 9600, 'None')
    assert client.post('/replot', data={'job': done}).status_code == 409   # a plot is running
