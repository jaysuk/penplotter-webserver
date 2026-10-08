"""The plot log (plotlog.py and where main.py / send2serial.py write to it) and the bytes-per-write setting."""
import os
import threading

import pytest

from test_routes import PLOT, wait_for, wait_until_plotting, slow_plot     # noqa: F401  (slow_plot is a fixture)
from test_send2serial import BIG_PLOT, SIO, SMALL_PLOT, run_stalled


def log_text(app):
    return app.plotlog.tail()


# ---- the file ---------------------------------------------------------------------------------

def test_lines_have_a_time_and_cannot_break_the_format(app):
    app.plotlog.log('first\nsecond\x00third')
    text = log_text(app)
    assert text.count('\n') == 0                          # one line, however the text looked
    assert 'first | second | third' in text
    assert text[:4].isdigit() and text[4] == '-'          # starts with the date


def test_a_long_line_is_cut(app):
    app.plotlog.log('x' * 5000)
    assert len(log_text(app)) < app.plotlog.MAX_LINE + 100


def test_tail_gives_the_last_lines_oldest_first(app):
    for n in range(10):
        app.plotlog.log('line %d' % n)
    lines = app.plotlog.tail(3).splitlines()
    assert [l.split('] ', 1)[1] for l in lines] == ['line 7', 'line 8', 'line 9']
    assert app.plotlog.tail('rubbish').count('\n') == 9    # a bad count means the default


def test_the_file_is_turned_over_and_the_old_one_still_read(app, monkeypatch):
    monkeypatch.setattr(app.plotlog, 'MAX_BYTES', 300)
    for n in range(20):
        app.plotlog.log('entry number %02d' % n)
    assert os.path.exists(app.plotlog.OLD)
    assert os.path.getsize(app.plotlog.PATH) < 600        # kept small
    text = app.plotlog.tail(5000)
    assert 'entry number 19' in text and 'entry number 15' in text
    assert app.plotlog.everything().count('entry number') >= 5


def test_clear_deletes_both_files(app, monkeypatch):
    monkeypatch.setattr(app.plotlog, 'MAX_BYTES', 100)
    for n in range(10):
        app.plotlog.log('entry %d' % n)
    assert app.plotlog.clear() is True
    assert app.plotlog.tail() == '' and not os.path.exists(app.plotlog.PATH) and not os.path.exists(app.plotlog.OLD)


def test_logging_never_raises(app, monkeypatch):
    monkeypatch.setattr(app.plotlog, 'PATH', os.path.join('uploads', 'a.hpgl', 'nope', 'plot.log'))
    (app.dir / 'uploads' / 'a.hpgl').write_text('x')       # a file where a folder is needed
    app.plotlog.log('this goes nowhere')
    assert app.plotlog.tail() == ''


# ---- routes -----------------------------------------------------------------------------------

def test_the_log_can_be_read_downloaded_and_cleared(app, client):
    app.plotlog.log('hello')
    assert 'hello' in client.get('/plot_log').get_data(as_text=True)
    download = client.get('/plot_log?download=1')
    assert 'attachment' in download.headers['Content-Disposition'] and 'hello' in download.get_data(as_text=True)
    assert client.get('/plot_log?lines=1').status_code == 200
    assert client.post('/plot_log/clear').status_code == 200
    assert client.get('/plot_log').get_data(as_text=True) == ''
    assert client.get('/plot_log/clear').status_code == 405          # changing state is POST only


def test_the_page_offers_the_log_and_the_setting(client):
    page = client.get('/').get_data(as_text=True)
    assert 'openPlotLog' in page and 'modal-plotlog' in page and 'name="plotter_chunk_size"' in page


# ---- what gets written ------------------------------------------------------------------------

def test_a_plot_leaves_its_story_in_the_log(app, client, uploads, slow_plot):
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_until_plotting(app)
    client.post('/pause_plot', environ_overrides={'REMOTE_ADDR': '10.1.2.3'})
    client.post('/resume_plot')
    assert client.post('/stop_plot', environ_overrides={'REMOTE_ADDR': '10.1.2.3'}).data == b'Plot stopped'
    assert wait_for(lambda: not app.main.plot_lock.locked())
    text = log_text(app)
    assert 'started: a.hpgl' in text
    assert 'log: Configured for' in text                         # what the page's log shows is in the file too
    assert 'Pause requested by the page at 10.1.2.3' in text and 'Resume requested by' in text
    assert 'Stop requested by the page at 10.1.2.3' in text
    assert 'ended: stopped' in text


def test_a_stop_from_the_shield_button_says_so(app, client, uploads, slow_plot):
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_until_plotting(app)
    app.main.button_stop()
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert 'Stop requested by a button of the Pi Plot shield' in log_text(app)


def test_a_crash_in_the_plot_is_logged_with_its_traceback(app, client, uploads, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError('boom from the sender')
    monkeypatch.setattr(app.send2serial, 'sendToPlotter', broken)
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: not app.main.plot_lock.locked())
    text = log_text(app)
    assert 'RuntimeError' in text and 'boom from the sender' in text and 'ended: failed' in text
    assert 'ERROR: Plot failed' in text


def test_the_sender_logs_how_it_ended(app, uploads):
    (uploads / 'p.hpgl').write_bytes(SMALL_PLOT)
    assert app.send2serial.sendToPlotter(SIO(), 'uploads/p.hpgl', '/dev/x', 9600, 'XON/XOFF') is True
    text = log_text(app)
    assert 'Sending p.hpgl' in text and '/dev/x at 9600 baud, XON/XOFF flow control, 30 bytes per write' in text
    assert 'Sender ended: the whole file was sent' in text


def test_a_plotter_that_holds_the_line_is_logged(app, uploads, monkeypatch):
    monkeypatch.setattr(app.send2serial, 'STALL_LOG_AFTER', 0.05)
    thread, result = run_stalled(app, type('P', (), {'uploads': uploads}), 'XON/XOFF')
    assert result == [True]
    text = log_text(app)
    assert 'The plotter is not taking data' in text and 'Gave up waiting' in text
    assert 'Sender ended: not finished' in text


def test_a_stall_that_ends_is_logged_too(app, uploads, monkeypatch):
    monkeypatch.setattr(app.send2serial, 'STALL_LOG_AFTER', 0.05)
    (uploads / 'slow.hpgl').write_bytes(SMALL_PLOT)
    app.serial.Serial.backlog = 4000
    threading.Timer(0.4, lambda: setattr(app.serial.Serial, 'backlog', 0)).start()
    assert app.send2serial.sendToPlotter(SIO(), 'uploads/slow.hpgl', '/dev/x', 9600, 'XON/XOFF') is True
    assert 'The plotter takes data again after' in log_text(app)


def test_a_serial_error_is_logged(app, uploads, monkeypatch):
    monkeypatch.setattr(app.send2serial, 'RECONNECT_DELAYS', (0.01,))
    monkeypatch.setattr(app.send2serial, 'RECONNECT_GIVE_UP', 0.05)
    (uploads / 'e.hpgl').write_bytes(BIG_PLOT)
    app.serial.Serial.break_at = 30                 # the cable comes out, and stays out
    app.serial.Serial.failures_after_break = 999
    assert app.send2serial.sendToPlotter(SIO(), 'uploads/e.hpgl', '/dev/x', 9600, 'XON/XOFF') is False
    text = log_text(app)
    assert 'Serial error after' in text and 'device disconnected' in text and 'Sender ended: not finished' in text


def test_a_heartbeat_shows_the_plot_is_alive(app, uploads, monkeypatch):
    monkeypatch.setattr(app.send2serial, 'HEARTBEAT_EVERY', 0)
    (uploads / 'h.hpgl').write_bytes(SMALL_PLOT)
    app.send2serial.sendToPlotter(SIO(), 'uploads/h.hpgl', '/dev/x', 9600, 'XON/XOFF')
    assert 'Still going:' in log_text(app) and 'bytes sent' in log_text(app)


# ---- bytes per write --------------------------------------------------------------------------

def writes(app, flow, content=SMALL_PLOT * 5):
    sizes = []
    app.serial.Serial.on_data = lambda port, data: sizes.append(len(data))
    (app.dir / 'uploads' / 'c.hpgl').write_bytes(content)
    assert app.send2serial.sendToPlotter(SIO(), 'uploads/c.hpgl', '/dev/x', 9600, flow) is True
    return sizes


def test_the_sender_decides_without_a_setting(app):
    assert max(writes(app, 'XON/XOFF')) == 30


@pytest.mark.parametrize('flow', ['XON/XOFF', 'NONE'])
def test_a_chosen_size_sets_the_write_size(app, flow):
    app.main.config.set('plotter', 'chunk_size', '200')
    sizes = writes(app, flow)
    assert max(sizes) == 200 and sizes[0] == 200
    app.main.config.set('plotter', 'chunk_size', '7')
    assert max(writes(app, flow)) == 7


def test_with_buffer_feedback_a_chosen_size_is_held_to_half_the_buffer(app):
    app.main.config.set('plotter', 'chunk_size', '1000')      # the fake plotter has a 1024 byte buffer
    assert max(writes(app, 'Software')) == 512


@pytest.mark.parametrize('value', ['0', '', 'abc', '-5', '5000', '1e3', '99999'])
def test_an_unusable_setting_means_automatic(app, value):
    app.main.config.set('plotter', 'chunk_size', value)
    assert max(writes(app, 'XON/XOFF')) == 30


def test_a_percent_sign_in_the_setting_does_not_break_the_plot(app):
    app.main.config.set('plotter', 'chunk_size', '5%%')
    assert max(writes(app, 'XON/XOFF')) == 30


def test_the_setting_is_saved_and_validated(app, client):
    assert client.get('/save_configfile').get_json()['plotter_chunk_size'] == '0'
    assert client.post('/save_configfile', data={'plotter_chunk_size': '120'}).status_code == 200
    assert client.get('/save_configfile').get_json()['plotter_chunk_size'] == '120'
    for bad in ('-1', '1025', 'x', '1.5', '12345'):
        assert client.post('/save_configfile', data={'plotter_chunk_size': bad}).status_code == 400
    assert client.post('/save_configfile', data={'plotter_chunk_size': ''}).status_code == 200
    assert app.send2serial.chunk_setting() == 0


def test_hpib_still_sends_a_byte_at_a_time(app):
    app.main.config.set('plotter', 'chunk_size', '200')
    assert app.send2serial.chunk_size('HP-IB', 1024, False) == 1
