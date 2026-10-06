"""Timelapse: pictures of the plotter taken while a plot runs, and a video made from them.

A `Recorder` takes a picture every few seconds from a camera (a snapshot address, `rpicam-still` or
`fswebcam`; never a command taken from the settings) into `timelapse/<id>/frame-00001.jpg`. When the
plot is over `render` makes `timelapse.mp4` with ffmpeg, if it is installed, and the pictures can be
downloaded as a zip either way. Nothing here may stop a plot: a camera that does not answer is
reported once and the recording gives up after a few misses.
"""
import configparser
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile

# Shared, live configuration object (updated when settings are saved in the UI)
from config import config

FOLDER = 'timelapse'
SOURCES = ('url', 'rpicam', 'fswebcam')
# <date>-<time>-<name of the plotted file>. The name cannot start with a dot, so an id is never a path
ID_RE = re.compile(r'[0-9]{8}-[0-9]{6}-[A-Za-z0-9_-][A-Za-z0-9._-]{0,59}')
URL_RE = re.compile(r'https?://[^\s\x00-\x1f]{1,500}')
# What can be fetched from a finished (or running) timelapse, and how it is sent
FILES = {'timelapse.mp4': 'video/mp4', 'frames.zip': 'application/zip', 'latest.jpg': 'image/jpeg'}

FRAME_NAME = 'frame-{:05d}.jpg'
FRAME_RE = re.compile(r'frame-([0-9]{5})\.jpg')
INFO = 'info.json'
VIDEO = 'timelapse.mp4'
POSTER = 'poster.jpg'

CAPTURE_TIMEOUT = 25            # seconds a camera may take for one picture
VIDEO_TIMEOUT = 4 * 3600        # a Pi Zero is slow
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_FRAMES = 10000              # enough for hours at the shortest interval, and a stop to a runaway disk
MIN_FREE_BYTES = 200 * 1024 * 1024
MAX_FAILURES = 5                # pictures in a row that did not work before the recording gives up


class CaptureError(Exception):
    """A picture could not be taken. The message is for the log, so it holds no addresses."""


# ////////////////////////////////////////////////////////////////////////////
# Settings

def _get(option, default=''):
    try:
        return config.get('timelapse', option, fallback=default).strip()
    except configparser.Error:
        return config.get('timelapse', option, raw=True, fallback=default).strip()


def _flag(option, default='false'):
    return _get(option, default).lower() == 'true'


def _number(option, default, low, high):
    try:
        return max(low, min(high, int(_get(option, str(default)))))
    except ValueError:
        return default


def enabled():
    return _flag('timelapse_enable')


def auto_start():
    """Record every plot, without being asked for each one."""
    return _flag('timelapse_auto_start')


def preview():
    """Show the latest picture on the page while plotting."""
    return _flag('timelapse_preview')


def keep_frames():
    """Keep the pictures after the video is made."""
    return _flag('timelapse_keep_frames')


def source():
    value = _get('timelapse_source', 'url')
    return value if value in SOURCES else 'url'


def url():
    return _get('timelapse_url')


def interval():
    return _number('timelapse_interval', 10, 1, 3600)


def fps():
    return _number('timelapse_fps', 25, 1, 60)


def tail():
    """Seconds to go on recording after the last byte is sent, for a plotter that is still drawing."""
    return _number('timelapse_tail', 10, 0, 600)


def camera_tool(name=None):
    """The program that takes pictures for the chosen source, or None."""
    name = name or source()
    if name == 'rpicam':
        return shutil.which('rpicam-still') or shutil.which('libcamera-still')
    if name == 'fswebcam':
        return shutil.which('fswebcam')
    return None


def problem():
    """Why recording cannot work with the current settings, or None."""
    if source() == 'url':
        if URL_RE.fullmatch(url()) is None:
            return 'Enter the address of the camera picture in the timelapse settings'
    elif camera_tool() is None:
        return {'rpicam': 'rpicam-still (libcamera-apps) is not installed',
                'fswebcam': 'fswebcam is not installed'}[source()]
    return None


def video_tool():
    return shutil.which('ffmpeg')


# ////////////////////////////////////////////////////////////////////////////
# Taking a picture

_opener = urllib.request.build_opener(urllib.request.HTTPHandler, urllib.request.HTTPSHandler)


def _fetch(address, path):
    parts = urllib.parse.urlsplit(address)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise CaptureError('The camera address must start with http:// or https://')
    try:
        with _opener.open(address, timeout=10) as response:
            data = response.read(MAX_FRAME_BYTES + 1)
    except Exception as e:      # the message of a urllib error can hold the address, with a password in it
        raise CaptureError('The camera did not answer ({})'.format(type(e).__name__))
    if len(data) > MAX_FRAME_BYTES:
        raise CaptureError('The camera address is not a single picture (is it a video stream?)')
    if not data.startswith(b'\xff\xd8\xff'):
        raise CaptureError('The camera did not send a JPEG picture')
    with open(path, 'wb') as f:
        f.write(data)


def _run_camera(command):
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                timeout=CAPTURE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise CaptureError('The camera took too long')
    except OSError as e:
        raise CaptureError('Could not run the camera program ({})'.format(type(e).__name__))
    if result.returncode != 0:
        print('timelapse camera failed:', result.stderr.decode('utf-8', 'replace')[:300])
        raise CaptureError('The camera program failed')


def capture(path):
    """Take one picture into `path`. Raises CaptureError."""
    partial = path + '.part'
    try:
        kind = source()
        if kind == 'url':
            _fetch(url(), partial)
        elif camera_tool() is None:
            raise CaptureError(problem())
        elif kind == 'rpicam':
            _run_camera([camera_tool(), '-n', '-t', '500', '--width', '1280', '--height', '720', '-q', '85',
                         '-e', 'jpg', '-o', partial])
        else:
            _run_camera([camera_tool(), '-q', '--no-banner', '-r', '1280x720', '--jpeg', '85', partial])
        if not os.path.isfile(partial) or os.path.getsize(partial) == 0:
            raise CaptureError('The camera made an empty picture')
        os.replace(partial, path)
    finally:
        try:
            os.remove(partial)
        except OSError:
            pass


# ////////////////////////////////////////////////////////////////////////////
# Folders

_active = set()                 # ids being recorded
_rendering = set()              # ids a video is made for
_lock = threading.Lock()


def make_id(name, when=None):
    base = re.sub(r'[^A-Za-z0-9._-]', '_', os.path.splitext(os.path.basename(name))[0]).lstrip('.-') or 'plot'
    return '{}-{}'.format(time.strftime('%Y%m%d-%H%M%S', time.localtime(when)), base[:60])


def folder(ident):
    """The folder of a timelapse, or None when `ident` is not an id (nothing from outside is joined to a path)."""
    if not isinstance(ident, str) or ID_RE.fullmatch(ident) is None:
        return None
    return os.path.join(FOLDER, ident)


def _read_info(path):
    try:
        with open(os.path.join(path, INFO)) as f:
            info = json.load(f)
        return info if isinstance(info, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_info(path, info):
    try:
        with open(os.path.join(path, INFO + '.tmp'), 'w') as f:
            json.dump(info, f)
        os.replace(os.path.join(path, INFO + '.tmp'), os.path.join(path, INFO))
    except OSError as e:
        print('timelapse: could not write', INFO, type(e).__name__)


def frame_numbers(path):
    try:
        return sorted(int(m.group(1)) for m in map(FRAME_RE.fullmatch, os.listdir(path)) if m)
    except OSError:
        return []


def _size(path):
    total = 0
    for here, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(here, name))
            except OSError:
                pass
    return total


def usage():
    """Bytes the timelapses use."""
    return _size(FOLDER)


def busy(ident):
    with _lock:
        return ident in _active or ident in _rendering


def list_all():
    """Every timelapse, newest first."""
    found = []
    try:
        names = os.listdir(FOLDER)
    except OSError:
        return found
    for ident in names:
        path = folder(ident)
        if path is None or not os.path.isdir(path):
            continue
        info = _read_info(path)
        numbers = frame_numbers(path)
        with _lock:
            recording, rendering = ident in _active, ident in _rendering
        has_video = os.path.isfile(os.path.join(path, VIDEO))
        if recording:
            state = 'recording'
        elif rendering:
            state = 'rendering'
        elif has_video:
            state = 'video'
        elif info.get('status') == 'recording':
            state = 'interrupted'
        else:
            state = 'frames'
        found.append({'id': ident, 'file': str(info.get('file') or ''), 'started': info.get('started'),
                      'frames': len(numbers) or info.get('frames') or 0, 'state': state, 'video': has_video,
                      'zip': bool(numbers), 'poster': os.path.isfile(os.path.join(path, POSTER)) or bool(numbers),
                      'size': _size(path), 'fps': info.get('fps'), 'interval': info.get('interval')})
    found.sort(key=lambda item: item['id'], reverse=True)
    return found


def latest_frame(ident):
    """Path of the newest picture of a timelapse (or its poster once the pictures are gone), or None."""
    path = folder(ident)
    if path is None:
        return None
    numbers = frame_numbers(path)
    if numbers:
        return os.path.join(path, FRAME_NAME.format(numbers[-1]))
    poster = os.path.join(path, POSTER)
    return poster if os.path.isfile(poster) else None


def file_path(ident, name):
    """Path of one of FILES for a timelapse, or None."""
    path = folder(ident)
    if path is None or name not in FILES:
        return None
    if name == 'latest.jpg':
        return latest_frame(ident)
    if name == 'frames.zip':
        return path if frame_numbers(path) else None       # made when asked for
    target = os.path.join(path, name)
    return target if os.path.isfile(target) else None


def delete(ident):
    """Remove a timelapse. Returns None, or why not."""
    path = folder(ident)
    if path is None or not os.path.isdir(path):
        return 'There is no such timelapse'
    if busy(ident):
        return 'That timelapse is still being recorded or made into a video'
    shutil.rmtree(path, ignore_errors=True)
    return None


def delete_all_older_than(seconds):
    """Remove the timelapses whose newest file is older than `seconds`. Returns the ids."""
    removed = []
    cutoff = time.time() - seconds
    for item in list_all():
        path = folder(item['id'])
        newest = 0
        for here, _, names in os.walk(path):
            for name in names:
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(here, name)))
                except OSError:
                    pass
        if newest < cutoff and delete(item['id']) is None:
            removed.append(item['id'])
    return removed


def make_zip(ident, target):
    """Write the pictures of a timelapse to the zip file `target`. Returns the number of pictures."""
    path = folder(ident)
    numbers = frame_numbers(path) if path else []
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_STORED) as archive:
        for number in numbers:
            name = FRAME_NAME.format(number)
            archive.write(os.path.join(path, name), name)
    return len(numbers)


# ////////////////////////////////////////////////////////////////////////////
# Recording

class Recorder:
    """Takes pictures on a thread until `stop()`. `emit(event, data)` is socketio's (or the plot's)
    emit, `paused()` says whether the plot is held (no pictures of an idle plotter) and `on_frame(n)`
    is told about each picture."""

    def __init__(self, name, emit=None, paused=None, on_frame=None):
        self.id = make_id(name)
        self.name = os.path.basename(name)
        self.path = folder(self.id)
        self.emit = emit or (lambda event, data=None: None)
        self.paused = paused or (lambda: False)
        self.on_frame = on_frame
        self.interval = interval()
        self.fps = fps()
        self.frames = 0
        self.gave_up = None
        self.started = time.time()
        self._stop = threading.Event()
        self._thread = None

    def _say(self, message):
        self.emit('status_log', {'data': 'Timelapse: ' + message})

    def start(self):
        """Begin recording. Returns None, or why it could not."""
        reason = problem()
        if reason:
            return reason
        try:
            os.makedirs(self.path, exist_ok=True)
        except OSError as e:
            return 'Could not make the timelapse folder ({})'.format(type(e).__name__)
        self._write('recording')
        with _lock:
            _active.add(self.id)
        self._thread = threading.Thread(target=self._run, name='timelapse', daemon=True)
        self._thread.start()
        self._say('recording a picture every {} s'.format(self.interval))
        return None

    def stop(self):
        """Take the last picture and finish. Returns the number of pictures."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(CAPTURE_TIMEOUT * 2)
        with _lock:
            _active.discard(self.id)
        self._write('done' if not self.gave_up else 'failed')
        return self.frames

    def _write(self, status):
        _write_info(self.path, {'file': self.name, 'started': self.started, 'ended': time.time() if status != 'recording' else None,
                                'frames': self.frames, 'interval': self.interval, 'fps': self.fps, 'status': status})

    def _room(self):
        if self.frames >= MAX_FRAMES:
            return 'it holds {} pictures, the most it takes'.format(MAX_FRAMES)
        try:
            if shutil.disk_usage(self.path).free < MIN_FREE_BYTES:
                return 'the disk is nearly full'
        except OSError:
            pass
        return None

    def _run(self):
        failures = 0
        while self.gave_up is None:
            stopping = self._stop.is_set()
            if stopping or not self.paused():
                full = self._room()
                if full:
                    self.gave_up = full
                    break
                path = os.path.join(self.path, FRAME_NAME.format(self.frames + 1))
                try:
                    capture(path)
                except CaptureError as e:
                    failures += 1
                    if failures == 1:
                        self._say(str(e))
                    if failures >= MAX_FAILURES:
                        self.gave_up = str(e)
                else:
                    failures = 0
                    self.frames += 1
                    if self.on_frame:
                        try:
                            self.on_frame(self.frames)
                        except Exception:
                            pass
            if stopping:
                break
            self._stop.wait(self.interval)
        if self.gave_up:
            self._say('stopped recording, {}'.format(self.gave_up))


# ////////////////////////////////////////////////////////////////////////////
# The video

def make_video(ident, rate=None):
    """Turn the pictures of a timelapse into `timelapse.mp4`. Returns None, or what went wrong."""
    path = folder(ident)
    if path is None or not os.path.isdir(path):
        return 'There is no such timelapse'
    ffmpeg = video_tool()
    if ffmpeg is None:
        return 'ffmpeg is not installed (sudo apt install ffmpeg), so only the pictures are kept'
    numbers = frame_numbers(path)
    if len(numbers) < 2:
        return 'There are too few pictures for a video'
    # ffmpeg reads a numbered sequence: number it again if a picture is missing
    if numbers != list(range(1, len(numbers) + 1)):
        for new, old in enumerate(numbers, 1):
            if new != old:
                os.replace(os.path.join(path, FRAME_NAME.format(old)), os.path.join(path, FRAME_NAME.format(new)))
    partial = os.path.join(path, VIDEO + '.part')
    command = [ffmpeg, '-y', '-loglevel', 'error', '-framerate', str(rate or fps()),
               '-i', os.path.join(path, 'frame-%05d.jpg'),
               '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2', '-c:v', 'libx264', '-preset', 'veryfast',
               '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-f', 'mp4', partial]
    nice = shutil.which('nice')
    if nice:
        command = [nice, '-n', '10'] + command
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, timeout=VIDEO_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 'Making the video took too long'
    except OSError as e:
        return 'Could not run ffmpeg ({})'.format(type(e).__name__)
    if result.returncode != 0 or not os.path.isfile(partial) or os.path.getsize(partial) == 0:
        print('ffmpeg failed:', result.stderr.decode('utf-8', 'replace')[:500])
        try:
            os.remove(partial)
        except OSError:
            pass
        return 'ffmpeg could not make the video'
    os.replace(partial, os.path.join(path, VIDEO))
    return None


def render(ident, emit, rate=None, keep=None):
    """Make the video of a timelapse (a slow job: run it on its own thread) and tidy up. Tells the
    page through `emit`. Returns None, or what went wrong."""
    path = folder(ident)
    with _lock:
        if path is None or ident in _rendering or ident in _active:
            return 'That timelapse is busy'
        _rendering.add(ident)
    emit('timelapse_changed', {'data': ident})
    try:
        emit('status_log', {'data': 'Timelapse: making the video...'})
        error = make_video(ident, rate)
        if error is None and not (keep_frames() if keep is None else keep):
            numbers = frame_numbers(path)
            if numbers:
                try:
                    shutil.copyfile(os.path.join(path, FRAME_NAME.format(numbers[-1])), os.path.join(path, POSTER))
                except OSError:
                    pass
                for number in numbers:
                    try:
                        os.remove(os.path.join(path, FRAME_NAME.format(number)))
                    except OSError:
                        pass
    finally:
        with _lock:
            _rendering.discard(ident)
    emit('status_log', {'data': 'Timelapse: the video is ready.' if error is None else 'Timelapse: ' + error})
    emit('timelapse_changed', {'data': ident})
    return error
