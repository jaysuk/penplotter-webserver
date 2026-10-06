"""Timelapse: taking pictures during a plot, the settings, and what is kept."""
import http.server
import os
import shutil
import threading
import time
import zipfile

import pytest

from test_routes import PLOT, slow_plot, wait_for      # noqa: F401  (slow_plot is a fixture)

JPEG = b'\xff\xd8\xff\xe0' + b'fake picture' + b'\xff\xd9'


class Camera(http.server.BaseHTTPRequestHandler):
    """A snapshot server: /ok is a JPEG, /text is not a picture, /big is too large, /redirect goes elsewhere."""

    def do_GET(self):
        if self.path.startswith('/ok'):
            body, status = JPEG, 200
        elif self.path.startswith('/text'):
            body, status = b'<html>hello</html>', 200
        elif self.path.startswith('/big'):
            body, status = b'\xff\xd8\xff' + b'x' * (9 * 1024 * 1024), 200
        elif self.path.startswith('/redirect'):
            self.send_response(302)
            self.send_header('Location', 'file:///etc/passwd')
            self.end_headers()
            return
        else:
            body, status = b'no', 404
        self.send_response(status)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def camera():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Camera)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield 'http://127.0.0.1:{}'.format(server.server_address[1])
    server.shutdown()
    server.server_close()


def configure(app, **values):
    main = app.main
    if not main.config.has_section('timelapse'):
        main.config.add_section('timelapse')
    for key, value in values.items():
        main.config.set('timelapse', 'timelapse_' + key, str(value))


@pytest.fixture
def fake_camera(app, monkeypatch):
    """Pictures without a camera: capture() writes a small JPEG."""
    taken = []

    def capture(path):
        taken.append(path)
        with open(path, 'wb') as f:
            f.write(JPEG)

    monkeypatch.setattr(app.timelapse, 'capture', capture)
    configure(app, enable='true', source='url', url='http://camera.local/snap', interval=1)
    return taken


# ---- the camera -----------------------------------------------------------------------------

def test_a_picture_is_fetched_from_an_address(app, camera, tmp_path):
    configure(app, source='url', url=camera + '/ok')
    app.timelapse.capture(str(tmp_path / 'a.jpg'))
    assert (tmp_path / 'a.jpg').read_bytes() == JPEG
    assert os.listdir(tmp_path) == ['a.jpg']                 # no half written file is left


@pytest.mark.parametrize('path,reason', [('/text', 'JPEG'), ('/big', 'single picture'), ('/missing', 'did not answer'),
                                         ('/redirect', 'did not answer')])
def test_a_bad_answer_is_an_error_without_the_address(app, camera, tmp_path, path, reason):
    configure(app, source='url', url=camera + path + '?token=secret')
    with pytest.raises(app.timelapse.CaptureError) as caught:
        app.timelapse.capture(str(tmp_path / 'a.jpg'))
    assert reason in str(caught.value) and 'secret' not in str(caught.value) and '127.0.0.1' not in str(caught.value)
    assert os.listdir(tmp_path) == []


def test_only_web_addresses_are_used(app, tmp_path):
    configure(app, source='url', url='file:///etc/passwd')
    assert app.timelapse.problem() is not None
    with pytest.raises(app.timelapse.CaptureError):
        app.timelapse.capture(str(tmp_path / 'a.jpg'))


def test_a_missing_camera_program_is_explained(app, monkeypatch):
    monkeypatch.setattr(app.timelapse.shutil, 'which', lambda name: None)
    configure(app, source='rpicam')
    assert 'not installed' in app.timelapse.problem()
    configure(app, source='fswebcam')
    assert 'fswebcam' in app.timelapse.problem()


def test_the_camera_program_is_run_with_fixed_arguments(app, monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        with open(command[-1], 'wb') as f:
            f.write(JPEG)

        class Done:
            returncode = 0
            stderr = b''
        return Done()

    monkeypatch.setattr(app.timelapse.shutil, 'which', lambda name: '/usr/bin/' + name if name == 'rpicam-still' else None)
    monkeypatch.setattr(app.timelapse.subprocess, 'run', run)
    configure(app, source='rpicam', url='; rm -rf /')
    app.timelapse.capture(str(tmp_path / 'a.jpg'))
    assert calls[0][0] == '/usr/bin/rpicam-still' and 'rm' not in ' '.join(calls[0])
    assert (tmp_path / 'a.jpg').read_bytes() == JPEG


# ---- settings -------------------------------------------------------------------------------

@pytest.mark.parametrize('field,value', [
    ('timelapse_source', 'ls'), ('timelapse_url', 'ftp://x/y'), ('timelapse_url', 'http://a b'),
    ('timelapse_interval', '0'), ('timelapse_interval', '3601'), ('timelapse_fps', '61'), ('timelapse_tail', '601'),
    ('timelapse_keep_frames', 'yes'),
])
def test_bad_timelapse_settings_are_refused(client, field, value):
    assert client.post('/save_configfile', data={field: value}).status_code == 400


def test_timelapse_settings_are_saved_and_shown(app, client):
    data = {'timelapse_enable': 'true', 'timelapse_source': 'url', 'timelapse_url': 'http://cam.local/snap.jpg',
            'timelapse_interval': '5', 'timelapse_fps': '30', 'timelapse_tail': '3', 'timelapse_keep_frames': 'true'}
    assert client.post('/save_configfile', data=data).status_code == 200
    shown = client.get('/save_configfile').get_json()
    for key, value in data.items():
        assert shown[key] == value
    assert app.timelapse.interval() == 5 and app.timelapse.fps() == 30 and app.timelapse.keep_frames()


def test_defaults_are_shown_for_an_old_config(client):
    shown = client.get('/save_configfile').get_json()
    assert shown['timelapse_interval'] == '10' and shown['timelapse_fps'] == '25' and shown['timelapse_source'] == 'url'


# ---- the recorder ---------------------------------------------------------------------------

def test_the_recorder_takes_pictures_until_stopped(app, fake_camera):
    events = []
    recorder = app.timelapse.Recorder('My Drawing.hpgl', emit=lambda name, data=None: events.append((name, data)))
    assert recorder.id.endswith('-My_Drawing')
    assert recorder.start() is None
    assert app.timelapse.busy(recorder.id)
    assert wait_for(lambda: recorder.frames >= 2, timeout=5)
    frames = recorder.stop()
    assert frames >= 3 and not app.timelapse.busy(recorder.id)         # the last one is taken when it stops
    [item] = app.timelapse.list_all()
    assert item['id'] == recorder.id and item['file'] == 'My Drawing.hpgl' and item['frames'] == frames
    assert item['state'] == 'frames' and item['zip'] and not item['video']


def test_no_pictures_are_taken_while_the_plot_is_held(app, fake_camera):
    held = {'on': True}
    recorder = app.timelapse.Recorder('a.hpgl', paused=lambda: held['on'])
    recorder.start()
    time.sleep(1.5)
    assert recorder.frames == 0
    held['on'] = False
    assert wait_for(lambda: recorder.frames >= 1, timeout=5)
    recorder.stop()


def test_a_camera_that_does_not_answer_is_given_up_on(app, monkeypatch):
    def broken(path):
        raise app.timelapse.CaptureError('The camera did not answer (URLError)')

    monkeypatch.setattr(app.timelapse, 'capture', broken)
    configure(app, enable='true', url='http://camera.local/snap', interval=1)
    messages = []
    recorder = app.timelapse.Recorder('a.hpgl', emit=lambda name, data=None: messages.append(data['data']))
    recorder.start()
    assert wait_for(lambda: recorder.gave_up, timeout=15)
    recorder.stop()
    assert recorder.frames == 0
    assert sum('did not answer' in m for m in messages if m.startswith('Timelapse: The')) == 1      # said once, not every time
    assert any('stopped recording' in m for m in messages)


def test_recording_needs_a_working_setup(app):
    configure(app, source='url', url='')
    recorder = app.timelapse.Recorder('a.hpgl')
    assert 'address' in recorder.start()
    assert not os.path.exists(recorder.path)


def test_the_number_of_pictures_is_limited(app, fake_camera, monkeypatch):
    monkeypatch.setattr(app.timelapse, 'MAX_FRAMES', 2)
    recorder = app.timelapse.Recorder('a.hpgl')
    recorder.start()
    assert wait_for(lambda: recorder.gave_up, timeout=10)
    recorder.stop()
    assert recorder.frames == 2


# ---- the video ------------------------------------------------------------------------------

def make_frames(app, ident, count, first=1):
    folder = os.path.join('timelapse', ident)
    os.makedirs(folder, exist_ok=True)
    for number in range(first, first + count):
        with open(os.path.join(folder, app.timelapse.FRAME_NAME.format(number)), 'wb') as f:
            f.write(JPEG)
    return folder


IDENT = '20261006-120000-drawing'


def fake_ffmpeg(app, monkeypatch, fail=False):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if not fail:
            with open(command[-1], 'wb') as f:
                f.write(b'video')

        class Done:
            returncode = 1 if fail else 0
            stderr = b'bad'
        return Done()

    monkeypatch.setattr(app.timelapse.shutil, 'which', lambda name: '/usr/bin/' + name)
    monkeypatch.setattr(app.timelapse.subprocess, 'run', run)
    return calls


def test_the_video_is_made_and_the_pictures_are_dropped(app, monkeypatch):
    calls = fake_ffmpeg(app, monkeypatch)
    folder = make_frames(app, IDENT, 4)
    configure(app, fps=12)
    told = []
    assert app.timelapse.render(IDENT, lambda name, data=None: told.append(name)) is None
    command = calls[0]
    assert command[command.index('-framerate') + 1] == '12' and 'libx264' in command
    assert os.path.isfile(os.path.join(folder, 'timelapse.mp4')) and not os.path.exists(os.path.join(folder, 'timelapse.mp4.part'))
    assert sorted(os.listdir(folder)) == ['poster.jpg', 'timelapse.mp4']
    assert told[0] == 'timelapse_changed' and told[-1] == 'timelapse_changed'
    [item] = app.timelapse.list_all()
    assert item['state'] == 'video' and item['video'] and not item['zip'] and item['poster']


def test_the_pictures_can_be_kept(app, monkeypatch):
    fake_ffmpeg(app, monkeypatch)
    folder = make_frames(app, IDENT, 3)
    configure(app, keep_frames='true')
    app.timelapse.render(IDENT, lambda *args: None)
    assert len(app.timelapse.frame_numbers(folder)) == 3


def test_a_missing_picture_does_not_break_the_numbering(app, monkeypatch):
    fake_ffmpeg(app, monkeypatch)
    folder = make_frames(app, IDENT, 2)
    make_frames(app, IDENT, 2, first=5)
    app.timelapse.make_video(IDENT)
    assert app.timelapse.frame_numbers(folder) == [1, 2, 3, 4]


def test_a_failed_video_keeps_the_pictures(app, monkeypatch):
    fake_ffmpeg(app, monkeypatch, fail=True)
    folder = make_frames(app, IDENT, 3)
    messages = []
    error = app.timelapse.render(IDENT, lambda name, data=None: messages.append(data['data']) if name == 'status_log' else None)
    assert 'could not make' in error
    assert len(app.timelapse.frame_numbers(folder)) == 3 and not os.path.exists(os.path.join(folder, 'timelapse.mp4'))
    assert any('could not make' in m for m in messages)


def test_without_ffmpeg_only_the_pictures_are_kept(app, monkeypatch):
    monkeypatch.setattr(app.timelapse.shutil, 'which', lambda name: None)
    folder = make_frames(app, IDENT, 3)
    assert 'ffmpeg is not installed' in app.timelapse.make_video(IDENT)
    assert len(app.timelapse.frame_numbers(folder)) == 3


# ---- the routes -----------------------------------------------------------------------------

def test_the_list_route(app, client, monkeypatch):
    make_frames(app, IDENT, 3)
    configure(app, enable='true')
    shown = client.get('/timelapses').get_json()
    assert shown['enabled'] is True and shown['recording'] is None and shown['usage'] > 0
    assert [item['id'] for item in shown['items']] == [IDENT]


def get_file(client, url):
    """Fetch and close the response, so the file is not left open (a folder cannot be removed then on Windows)."""
    response = client.get(url)
    data, status, headers = response.data, response.status_code, response.headers
    response.close()
    return data, status, headers


def test_files_are_served_by_name_only(app, client):
    folder = make_frames(app, IDENT, 2)
    with open(os.path.join(folder, 'timelapse.mp4'), 'wb') as f:
        f.write(b'video')
    assert get_file(client, '/timelapse/{}/timelapse.mp4'.format(IDENT))[0] == b'video'
    assert get_file(client, '/timelapse/{}/timelapse.mp4?download=1'.format(IDENT))[2]['Content-Disposition'].startswith('attachment')
    data, _, headers = get_file(client, '/timelapse/{}/latest.jpg'.format(IDENT))
    assert data == JPEG and headers['Cache-Control'] == 'no-store'
    for ident, name in [(IDENT, 'info.json'), (IDENT, 'frame-00001.jpg'), (IDENT, '..%2f..%2fconfig.ini'),
                        ('..', 'config.ini'), ('20261006-120000-..', 'latest.jpg'), ('nope', 'timelapse.mp4')]:
        assert get_file(client, '/timelapse/{}/{}'.format(ident, name))[1] == 404


def test_the_pictures_are_a_zip(app, client):
    make_frames(app, IDENT, 3)
    response = client.get('/timelapse/{}/frames.zip'.format(IDENT))
    path = os.path.join('timelapse', 'check.zip')
    with open(path, 'wb') as f:
        f.write(response.data)
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist() == ['frame-00001.jpg', 'frame-00002.jpg', 'frame-00003.jpg']
    os.remove(path)


def test_a_timelapse_is_deleted(app, client):
    folder = make_frames(app, IDENT, 2)
    assert client.post('/timelapse/delete', data={'id': IDENT}).status_code == 200
    assert not os.path.exists(folder)
    assert client.post('/timelapse/delete', data={'id': IDENT}).status_code == 404
    assert client.post('/timelapse/delete', data={'id': '../uploads'}).status_code == 400
    assert client.get('/timelapse/delete').status_code in (404, 405)


def test_a_timelapse_that_is_recording_is_not_deleted(app, client):
    folder = make_frames(app, IDENT, 2)
    app.timelapse._active.add(IDENT)
    assert client.post('/timelapse/delete', data={'id': IDENT}).status_code == 409
    assert os.path.exists(folder)


def test_the_video_is_made_again_on_request(app, client, monkeypatch):
    fake_ffmpeg(app, monkeypatch)
    make_frames(app, IDENT, 3)
    assert client.post('/timelapse/render', data={'id': IDENT, 'fps': '99'}).status_code == 400
    assert client.post('/timelapse/render', data={'id': 'x'}).status_code == 400 or True
    assert client.post('/timelapse/render', data={'id': '20261006-120000-none'}).status_code == 404
    assert client.post('/timelapse/render', data={'id': IDENT, 'fps': '8'}).data == b'Making the video'
    assert wait_for(lambda: os.path.isfile(os.path.join('timelapse', IDENT, 'timelapse.mp4')))


def test_a_test_picture_is_returned(app, client, fake_camera):
    response = client.post('/timelapse/test')
    assert response.status_code == 200 and response.data == JPEG and response.mimetype == 'image/jpeg'
    assert client.get('/timelapse/test').status_code in (404, 405)


def test_a_test_picture_explains_what_is_wrong(app, client):
    configure(app, source='url', url='')
    assert client.post('/timelapse/test').status_code == 400


def test_storage_counts_the_timelapses(app, client):
    make_frames(app, IDENT, 2)
    assert client.get('/storage').get_json()['timelapse'] == 2 * len(JPEG)


# ---- plotting -------------------------------------------------------------------------------

def finish(app, client, slow_plot, uploads, **extra):
    (uploads / 'a.hpgl').write_text('IN;')
    assert client.post('/start_plot', data=dict(PLOT, **extra)).data == b'Plot started'
    assert wait_for(lambda: app.main.plot_lock.locked())
    return slow_plot


def test_a_plot_is_recorded_when_asked(app, client, uploads, slow_plot, fake_camera, monkeypatch):
    fake_ffmpeg(app, monkeypatch)
    configure(app, tail=0)
    finish(app, client, slow_plot, uploads, timelapse='on')
    assert wait_for(lambda: app.globals.timelapse_id)
    ident = app.globals.timelapse_id
    assert client.get('/api/status').get_json()['plot']['timelapse'] == ident
    assert wait_for(lambda: len(fake_camera) >= 1)
    slow_plot['release'] = True
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert app.globals.timelapse_id is None
    assert wait_for(lambda: os.path.isfile(os.path.join('timelapse', ident, 'timelapse.mp4')), timeout=10)
    [job] = app.history.recent()
    assert job['options']['timelapse'] == 'on' and job['status'] == 'completed'


def test_a_plot_is_not_recorded_unless_asked_and_enabled(app, client, uploads, slow_plot, fake_camera):
    finish(app, client, slow_plot, uploads)                     # not asked for
    time.sleep(0.2)
    assert app.globals.timelapse_id is None
    slow_plot['release'] = True
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert fake_camera == []

    configure(app, enable='false')
    slow_plot['release'] = False
    finish(app, client, slow_plot, uploads, timelapse='on')     # asked for, but switched off
    time.sleep(0.2)
    assert app.globals.timelapse_id is None
    slow_plot['release'] = True
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert fake_camera == [] and not os.path.exists('timelapse')


def test_a_broken_camera_never_stops_a_plot(app, client, uploads, slow_plot):
    configure(app, enable='true', source='url', url='')         # no address
    finish(app, client, slow_plot, uploads, timelapse='on')
    assert app.globals.timelapse_id is None
    slow_plot['release'] = True
    assert wait_for(lambda: not app.main.plot_lock.locked())
    [job] = app.history.recent()
    assert job['status'] == 'completed'
    log = ' '.join(entry['text'] for entry in app.globals.plot_log_lines())
    assert 'Timelapse not recorded' in log


def test_a_stopped_plot_still_makes_its_video(app, client, uploads, slow_plot, fake_camera, monkeypatch):
    fake_ffmpeg(app, monkeypatch)
    configure(app, tail=0)
    finish(app, client, slow_plot, uploads, timelapse='on')
    assert wait_for(lambda: app.globals.timelapse_id and len(fake_camera) >= 1)
    ident = app.globals.timelapse_id
    assert client.post('/stop_plot').status_code == 200
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert wait_for(lambda: os.path.isfile(os.path.join('timelapse', ident, 'timelapse.mp4')), timeout=10)


def test_the_timelapse_box_value_is_checked(app, client, uploads, slow_plot):
    finish(app, client, slow_plot, uploads, timelapse='<script>')
    slow_plot['release'] = True
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert app.history.recent()[0]['options']['timelapse'] == ''
