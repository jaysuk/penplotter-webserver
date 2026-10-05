import configparser
import hmac
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import traceback
import zipfile
from urllib.parse import urlparse

# The compiled modules (config, send2serial, convert_vpype) use paths relative to
# the application directory, so always run from there.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

from flask import Flask, Response, render_template, request, send_from_directory, jsonify
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO, emit

import backup
import globals
import history
import hpgl_analysis
import notification
import plot_queue
import plotter_control
import presets
import send2serial
import tasmota
import text_drawing
import vpype_plugins
from convert_vpype import convert_file, output_name, create_text as make_text_svg
from config import config
# import RPi.GPIO as GPIO

globals.initialize()
history.init()
presets.init()
plot_queue.init()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024
app.config['UPLOAD_EXTENSIONS'] = ['.svg', '.hpgl', '.cal']
app.config['UPLOAD_PATH'] = 'uploads'
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or secrets.token_hex(32)

# Debug mode (interactive debugger + reloader) must be explicitly requested
DEBUG = os.environ.get('WEBPLOTTER_DEBUG') == '1'

socketio = SocketIO(app)

# Buttons setup

# GPIO.setmode(GPIO.BCM)
# GPIO.setup(27, GPIO.IN, pull_up_down=GPIO.PUD_UP)
# GPIO.setup(22, GPIO.IN, pull_up_down=GPIO.PUD_UP)

DEVICES = {'hp7475a', 'hp7440a', 'hp7550', 'dxy', 'sketchmate', 'dmp_161',
           'designmate', 'artisan', 'mp4200'}
FLOW_CONTROLS = {'CTS/RTS', 'HP-IB', 'XON/XOFF', 'Software', 'None'}
OUTPUT_SIZES = {'a0', 'a1', 'a2', 'a3', 'a4'}
ORIENTATIONS = {'portrait', 'landscape'}
ROTATIONS = {'0', '90', '180', '270'}
MARGIN_RE = re.compile(r'[0-9]{1,2}(\.[0-9])?')
MAX_MARGIN_MM = 50
TEXT_SIZE_RE = re.compile(r'[0-9]{1,3}(\.[0-9])?')
TEXT_SIZE_MM = (3, 200)
PENS_RE = re.compile(r'[0-9]{1,2}(,[0-9]{1,2}){0,15}')
PEN_CHANGES = {'pause', 'auto'}
PORT_RE = re.compile(r'^(/dev/[\w./-]+|COM\d+)$')
SPEED_RE = re.compile(r'^(\d+(\.\d+)?)?$')
HOST_RE = re.compile(r'^([A-Za-z0-9.-]+(:\d{1,5})?)?$')
CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f]')

# Custom vpype commands are run by vpype, so only allow plain words and numbers (no quotes,
# %expressions%, paths or shell characters) and none of the commands that run code, touch
# files or open a window.
CUSTOM_COMMAND_RE = re.compile(r'^[A-Za-z0-9 ._=+-]*$')
BLOCKED_VPYPE_COMMANDS = {'eval', 'script', 'read', 'write', 'forfile', 'include', 'show'}

SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')

# Held for the whole duration of a plot
plot_lock = threading.Lock()
current_plot = None

config_lock = threading.Lock()

# Copies of plots with only some of the pens, kept just for the time they are plotted
PLOT_CACHE = os.path.join('cache', 'plots')
# Conversions that were previewed but not saved yet
PREVIEW_DIR = os.path.join('cache', 'preview')
PREVIEW_MAX_AGE = 24 * 3600
# PDFs are turned into svgs with poppler's pdftocairo (an apt package, so only if it is installed)
PDF_TOOL = 'pdftocairo'
PDF_DIR = os.path.join('cache', 'pdf')
PDF_TIMEOUT = 120
HPGL_NAME_RE = re.compile(r'[A-Za-z0-9._-]+\.hpgl')
# What can be plotted. A .cal file (CalComp) is sent as it is: no analysis, preview, pen choice or resume
PLOT_EXTENSIONS = ('.hpgl', '.cal')
# Analysing a bigger file takes long enough on a Pi to be worth a line in the log
SLOW_ANALYSIS_BYTES = 1024 * 1024

for _section in ('telegram', 'tasmota', 'timelapse', 'plotter', 'notifications'):
    if not config.has_section(_section):
        config.add_section(_section)


# ////////////////////////////////////////////////////////////////////////////
# Access control

def config_value(section, option):
    """A config value as written by the UI ('%' doubled). A hand edited file may contain a single
    '%', which configparser refuses to interpolate, so fall back to the text as it is."""
    try:
        return config.get(section, option, fallback='')
    except configparser.InterpolationError:
        return config.get(section, option, raw=True, fallback='')


def auth_configured():
    return bool(config_value('auth', 'username') and config_value('auth', 'password'))


def is_authorized():
    """Optional HTTP basic auth, enabled by setting [auth] username/password in config.ini."""
    if not auth_configured():
        return True
    auth = request.authorization
    if auth is None:
        return False
    user_ok = hmac.compare_digest((auth.username or '').encode('utf-8'),
                                  config_value('auth', 'username').encode('utf-8'))
    pass_ok = hmac.compare_digest((auth.password or '').encode('utf-8'),
                                  config_value('auth', 'password').encode('utf-8'))
    return user_ok and pass_ok


@app.before_request
def guard_request():
    if not is_authorized():
        return Response('Authentication required', 401,
                        {'WWW-Authenticate': 'Basic realm="Web Plotter"'})

    # CSRF: refuse state-changing requests that a browser reports as coming from another site
    if request.method not in SAFE_METHODS:
        origin = request.headers.get('Origin') or request.headers.get('Referer')
        if origin and urlparse(origin).netloc != request.host:
            return 'Cross-site request refused', 403


# ////////////////////////////////////////////////////////////////////////////
# Helpers

def make_tree(path):
    tree = dict(name=os.path.basename(path), content=[])
    try: lst = os.listdir(path)
    except OSError:
        pass #ignore errors
    else:
        lst = sorted(lst)
        for name in lst:
            fn = os.path.join(path, name)
            if os.path.isdir(fn):
                tree['content'].append(make_tree(fn))
            else:
                if (name != '.gitignore'):
                    entry = dict(name=name)
                    try:
                        stat = os.stat(fn)
                        entry.update(size=stat.st_size, mtime=stat.st_mtime)
                    except OSError:
                        pass
                    tree['content'].append(entry)
    return tree


def upload_file_path(name):
    """Map a user supplied name to 'uploads/<name>', or None if it escapes the upload directory."""
    if not name or not isinstance(name, str):
        return None
    root = os.path.realpath(app.config['UPLOAD_PATH'])
    full = os.path.realpath(os.path.join(root, name))
    try:
        if os.path.commonpath([root, full]) != root or full == root:
            return None
    except ValueError:
        return None
    return app.config['UPLOAD_PATH'] + '/' + os.path.relpath(full, root).replace(os.sep, '/')


def valid_baudrate(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9]+', value) is not None and 300 <= int(value) <= 921600


def check_vpype_command(command):
    """Return an error message if a custom vpype command line is not allowed, else None."""
    if not command:
        return None
    if len(command) > 200:
        return 'Custom vpype command is too long'
    if not CUSTOM_COMMAND_RE.fullmatch(command):
        return 'Custom vpype commands may only contain letters, numbers, spaces and . _ = + -'
    file_commands = vpype_plugins.file_commands()
    for token in command.split():
        word = token.lstrip('-').lower()
        if word in BLOCKED_VPYPE_COMMANDS:
            return 'The vpype command "{}" is not allowed'.format(token)
        if word in file_commands:
            return 'The plugin command "{}" reads or writes files, which is not allowed here'.format(token)
    return None


def conversion_options(form):
    """Validated conversion options from a form: (options, None), or (None, error message)."""
    outputsize = form.get('outputsize')
    pageorientation = form.get('pageorientation')
    device = form.get('device')
    speed = form.get('speed') or ''
    custom_comand = form.get('command_input') or ''
    margin = form.get('margin') or '0'
    rotate = form.get('rotate') or '0'

    if outputsize not in OUTPUT_SIZES:
        return None, 'Invalid output size'
    if pageorientation not in ORIENTATIONS:
        return None, 'Invalid page orientation'
    if device not in DEVICES:
        return None, 'Invalid plotter device'
    if not SPEED_RE.fullmatch(speed):
        return None, 'Invalid plot speed'
    if not MARGIN_RE.fullmatch(margin) or float(margin) > MAX_MARGIN_MM:
        return None, 'Invalid margin (0 to {} mm)'.format(MAX_MARGIN_MM)
    if rotate not in ROTATIONS:
        return None, 'Invalid rotation'
    error = check_vpype_command(custom_comand)
    if error:
        return None, error
    return {
        'outputsize': outputsize, 'pageorientation': pageorientation, 'device': device, 'speed': speed,
        'custom_comand': custom_comand, 'margin': float(margin), 'rotate': int(rotate),
        'linemerge': bool(form.get('linemerge')), 'linesort': bool(form.get('linesort')),
        'linesimplify': bool(form.get('linesimplify')), 'reloop': bool(form.get('reloop')),
        'mirror_x': bool(form.get('mirror_x')), 'mirror_y': bool(form.get('mirror_y')),
    }, None


def preset_form(options):
    """Validated options as the convert form holds them, to be stored in a preset."""
    return {
        'outputsize': options['outputsize'], 'pageorientation': options['pageorientation'],
        'device': options['device'], 'speed': options['speed'], 'command_input': options['custom_comand'],
        'margin': '{:g}'.format(options['margin']), 'rotate': str(options['rotate']),
        'linemerge': options['linemerge'], 'linesort': options['linesort'],
        'linesimplify': options['linesimplify'], 'reloop': options['reloop'],
        'mirror_x': options['mirror_x'], 'mirror_y': options['mirror_y'],
    }


def run_conversion(file, options, output=None):
    """Convert an svg with validated options. Returns the message for the UI."""
    return convert_file(file, options['outputsize'], options['pageorientation'], options['device'],
                        options['speed'], options['custom_comand'], options['linemerge'], options['linesort'],
                        options['linesimplify'], options['reloop'], socketio,
                        margin=options['margin'], rotate=options['rotate'],
                        mirror_x=options['mirror_x'], mirror_y=options['mirror_y'], output=output)


class PlotEvents:
    """Stands in for socketio during a plot: sends each event on and remembers it, so a page that
    is refreshed (or opened on another device) mid-plot can be brought up to date."""

    def emit(self, name, data=None, **kwargs):
        globals.record_event(name, data)
        socketio.emit(name, data, **kwargs)
        if name in ('pen_change', 'wait_change'):
            broadcast_plot_state()      # the sender paused the plot: tell every page


def plot_state():
    return globals.plot_state(plot_lock.locked(), current_plot)


def broadcast_plot_state():
    socketio.emit('plot_state', {'data': plot_state()})


def last_error():
    for entry in reversed(globals.plot_log_lines()):
        if entry['type'] == 'error':
            return entry['text']
    return None


def wait_seconds(events, seconds, message, stoppable):
    """Wait in short steps (the plot stays stoppable). Stop skips the wait when `stoppable`."""
    if seconds <= 0:
        return
    events.emit('status_log', {'data': message})
    end = time.time() + seconds
    while time.time() < end and not (stoppable and globals.stop_requested):
        time.sleep(0.25)


def prepare_plot_file(events, file, analysis, pens):
    """The file to send and its analysis: the original, or a copy with only the chosen pens.

    Returns (path, analysis, temporary), `temporary` being a file to delete afterwards."""
    if analysis is None:
        if os.path.getsize(file) > SLOW_ANALYSIS_BYTES and not hpgl_analysis.cached(file):
            events.emit('status_log', {'data': 'Analysing the file...'})
        analysis = hpgl_analysis.analyze_cached(file)
    if not pens:
        return file, analysis, None
    if analysis is None:
        raise ValueError('This file is too large to pick pens from')
    os.makedirs(PLOT_CACHE, exist_ok=True)
    path = os.path.join(PLOT_CACHE, '{}-pens-{}.hpgl'.format(
        os.path.splitext(os.path.basename(file))[0], '-'.join(str(pen) for pen in pens)))
    hpgl_analysis.filter_pens(file, path, pens, analysis)
    events.emit('status_log', {'data': 'Plotting pen{} {} only.'.format(
        's' if len(pens) > 1 else '', ', '.join(str(pen) for pen in pens))})
    return path, hpgl_analysis.analyze(path), path


def wait_for_paper_change(events):
    """Hold back between two plots of the queue until Resume is pressed (Stop holds the queue)."""
    globals.wait_reason = 'paper_change'
    globals.paused = True
    events.emit('status_log', {'data': 'Change the paper, then press Resume to plot the next file.'})
    broadcast_plot_state()
    plotter_name = config.get('plotter', 'name', fallback='Plotter')
    notification.send('attention', '{}: {}: Change the paper'.format(plotter_name, current_plot), file=current_plot)
    while globals.paused and not globals.stop_requested:
        time.sleep(0.25)
    globals.clear_wait()


def resume_point(outcome, resume_start=0, resume_skip=0):
    """Where a stopped or failed plot could carry on, as a byte offset in the file it was made from
    (the original, or the copy with the chosen pens), or None.

    The sender counts bytes of the file it sent. A resumed plot sent a different file (the set-up
    commands, then the rest of the original from `resume_start`), so its count is mapped back."""
    if outcome not in ('stopped', 'failed'):
        return None
    if globals.sent_offset == 0:
        return None         # nothing was sent: there is nothing to carry on from
    sent = max(globals.sent_offset - globals.buffer_used, 0)
    if resume_start or resume_skip:
        sent = resume_start + max(sent - resume_skip, 0)
    return sent or None


def plot(file, port, baudrate, flowControl, poweroff, timelapse, pens=None, pen_change='auto', analysis=None,
         options=None, power_on=True, keep_power=None, paper_change=False, resume_from=None, resume_job=None):
    """Run a plot. Runs in a background task; the caller must already hold plot_lock.

    The queue runs several plots in a row: `power_on` is false when the plotter was left switched
    on by the previous plot, `keep_power` is a function that tells whether another plot follows
    (then the plotter is not switched off) and `paper_change` waits for Resume before that next
    plot. `resume_from` carries on a stopped or failed plot from that byte of the file (or of the
    copy with the chosen pens), `resume_job` being the history entry it continues. Returns how it ended: 'completed', 'stopped' or 'failed'."""
    global current_plot
    events = PlotEvents()
    cal = send2serial.is_cal(file)
    # The "shut down when finished" box is ticked by default and the page sends it even when Tasmota
    # is switched off in the settings: without a Tasmota there is nothing to wait for
    if not tasmota.enabled():
        poweroff = ''
    try:
        file_size = os.path.getsize(file)
    except OSError:
        file_size = None
    job = history.start(os.path.basename(file), port, baudrate, flowControl, options, file_size)
    outcome, error = None, None
    temporary = None
    resume_start, resume_skip = 0, 0
    try:
        # Lock editing while printing
        socketio.emit('lock_edit', {'data': 'on'})
        broadcast_plot_state()

        # Work out how long the plot takes (and which pens it uses) before anything is switched
        # on. A file that cannot be analysed still plots, just without a time left or pen change
        # pauses.
        send_path = file
        if cal:
            analysis = None     # not HP-GL: it is sent as it is
            if pens or resume_from:
                raise ValueError('Pens cannot be picked, and a plot cannot be resumed, in a .cal file')
        else:
            try:
                send_path, analysis, temporary = prepare_plot_file(events, file, analysis, pens)
            except (OSError, ValueError) as e:
                raise ValueError('Could not prepare the plot: ' + str(e))
        if resume_from:
            # Carry on from there: the rest of the file, after what sets the plotter up again
            os.makedirs(PLOT_CACHE, exist_ok=True)
            resume_path = os.path.join(PLOT_CACHE, os.path.splitext(os.path.basename(file))[0] + '-resume.hpgl')
            try:
                resume_start, resume_skip = hpgl_analysis.resume_file(send_path, resume_path, resume_from)
            except (OSError, ValueError) as e:
                raise ValueError('Could not resume: ' + str(e))
            if temporary:
                os.remove(temporary)        # the copy with the chosen pens: the resume file replaces it
            send_path, temporary = resume_path, resume_path
            analysis = hpgl_analysis.analyze(send_path)
            events.emit('status_log', {'data': 'Resuming from byte {} of the file.'.format(resume_start)})
        globals.cursor_ok = not cal and os.path.abspath(send_path) == os.path.abspath(file)
        broadcast_plot_state()      # pages learn now that they can watch this plot
        if analysis is None and pen_change == 'pause':
            events.emit('status_log', {'data': 'The file is too large to analyse: no time left, no pen change pauses.'})
        if analysis is not None:
            history.set_estimate(job, analysis['seconds'])

        # Tasmota - switch the plotter on and give it time to start up
        if poweroff == 'on' and power_on:
            tasmota.tasmota_setStatus(events, 'on')
            wait_seconds(events, tasmota.on_delay(),
                         'Waiting {} s for the plotter to start up (Stop cancels the plot)'.format(tasmota.on_delay()),
                         stoppable=True)

        # Start printing (unless Stop was pressed while the plotter was starting up)
        if globals.stop_requested:
            events.emit('status_log', {'data': '*** Plot stopped.'})
            outcome = 'stopped'
        else:
            result = send2serial.sendToPlotter(events, str(send_path), str(port), int(baudrate), str(flowControl),
                                               analysis=analysis, pen_pause=(pen_change == 'pause'),
                                               correction=history.correction())
            if result is False:
                outcome, error = 'failed', last_error()
            else:
                outcome = 'stopped' if globals.stop_requested else 'completed'
        globals.plot_finished = True

        # In a queue: the next plot follows, so the plotter stays on and the paper may be changed
        another_follows = outcome == 'completed' and keep_power is not None and keep_power()
        if another_follows and paper_change:
            wait_for_paper_change(events)

        # Tasmota - turn the plotter off, once it has had time to finish drawing. Flow control
        # without buffer feedback can have a lot of the plot still queued in the plotter when the
        # last byte is sent, so this is a delay you set (Stop skips it).
        if poweroff == 'on' and another_follows:
            events.emit('status_log', {'data': 'Leaving the plotter on for the next plot.'})
        elif poweroff == 'on':
            if outcome == 'stopped':
                # Stopped plots have been told to abort; just let the pen lift
                wait_seconds(events, 2, 'Switching the plotter off in 2 s', stoppable=False)
            else:
                wait_seconds(events, tasmota.off_delay(),
                             'Waiting {} s for the plotter to finish before switching it off '
                             '(Stop skips the wait)'.format(tasmota.off_delay()), stoppable=True)
            print("Sending power off command to Tasmota")
            tasmota.tasmota_setStatus(events, 'off')
    except Exception as e:
        traceback.print_exc()
        events.emit('error', {'data': 'Plot failed: ' + repr(e)})
        if outcome is None:
            outcome, error = 'failed', repr(e)
    finally:
        history.finish(job, outcome or 'failed', globals.plot_progress, error,
                       drawn_s=globals.drawn_seconds if outcome == 'completed' else None,
                       resume_offset=None if cal else resume_point(outcome, resume_start, resume_skip))
        if resume_job is not None and (outcome == 'completed' or globals.sent_offset > 0):
            history.clear_resume(resume_job)       # carried on: the old plot is not resumable again
        if temporary:
            try:
                os.remove(temporary)
            except OSError:
                pass
        if outcome == 'failed':
            plotter_name = config.get('plotter', 'name', fallback='Plotter')
            notification.send('error', '{}: {}: Failed: {}'.format(
                plotter_name, os.path.basename(file), error or last_error() or 'see the log'), file=os.path.basename(file))
        globals.printing = False
        globals.clear_wait()
        globals.current_file = 'None'
        current_plot = None
        plot_lock.release()
        # Unlock editing
        socketio.emit('lock_edit', {'data': 'off'})
        broadcast_plot_state()
    return outcome or 'failed'


# ////////////////////////////////////////////////////////////////////////////
# Buttons :TODO - something useful with buttons
def start_button(channel):
    socketio.emit('status_log', {'data': 'Button 2 was pushed!'})

def stop_button(channel):
    socketio.emit('status_log', {'data': 'Button 1 was pushed!'})

# GPIO.add_event_detect(27,GPIO.RISING,callback=start_button)
# GPIO.add_event_detect(22,GPIO.RISING,callback=stop_button)

@app.errorhandler(413)
def too_large(e):
    return "File is too large", 413

def pdf_import_available():
    return shutil.which(PDF_TOOL) is not None


def pdf_to_svg(pdf_path, svg_path):
    """Turn the first page of a PDF into an svg. Returns None, or what went wrong."""
    tool = shutil.which(PDF_TOOL)
    if tool is None:
        return 'PDF import needs poppler-utils (sudo apt install poppler-utils)'
    try:
        result = subprocess.run([tool, '-svg', '-f', '1', '-l', '1', pdf_path, svg_path],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                timeout=PDF_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 'Reading the PDF took too long'
    except OSError as e:
        return 'Could not run ' + PDF_TOOL + ': ' + str(e)
    if result.returncode != 0 or not os.path.isfile(svg_path) or os.path.getsize(svg_path) == 0:
        print('pdftocairo failed:', result.stderr.decode('utf-8', 'replace')[:500])
        return 'Could not read the PDF'
    return None


@app.route('/')
def index():
    files = make_tree(app.config['UPLOAD_PATH'])
    return render_template('index.html', files=files, pdf_import=pdf_import_available())


# Upload
@app.route('/', methods=['POST'])
def upload_files():
    uploaded_file = request.files.get('file')
    if uploaded_file is None:
        return 'No file received', 400
    filename = secure_filename(uploaded_file.filename or '')
    base, ext = os.path.splitext(filename)
    ext = ext.lower()
    if base and ext == '.pdf':
        return import_pdf(uploaded_file, base)
    if not base or ext not in app.config['UPLOAD_EXTENSIONS']:
        return 'Only .svg, .hpgl and .cal files are accepted', 400
    filename = base + ext
    if plot_lock.locked() and filename == current_plot:
        return 'This file is currently being plotted', 409
    uploaded_file.save(os.path.join(app.config['UPLOAD_PATH'], filename))
    return '', 204

def import_pdf(uploaded_file, base):
    """Keep the first page of an uploaded PDF as uploads/<base>.svg. The PDF itself is not kept."""
    if not pdf_import_available():
        return 'PDF import needs poppler-utils (sudo apt install poppler-utils)', 400
    if uploaded_file.stream.read(5) != b'%PDF-':
        return 'That is not a PDF file', 400
    uploaded_file.stream.seek(0)
    os.makedirs(PDF_DIR, exist_ok=True)
    pdf_path = os.path.join(PDF_DIR, base + '.pdf')
    svg_path = os.path.join(PDF_DIR, base + '.svg')
    try:
        uploaded_file.save(pdf_path)
        error = pdf_to_svg(pdf_path, svg_path)
        if error:
            return error, 400
        os.replace(svg_path, os.path.join(app.config['UPLOAD_PATH'], base + '.svg'))
    finally:
        for path in (pdf_path, svg_path):
            try:
                os.remove(path)
            except OSError:
                pass
    socketio.emit('status_log', {'data': 'Imported page 1 of {}.pdf. Filled shapes are drawn as outlines.'.format(base)})
    return '', 204


@app.route('/uploads/<filename>')
def upload(filename):
    return send_from_directory(app.config['UPLOAD_PATH'], filename)

@app.route('/timelapse/<filename>')
def timelapse(filename):
    return send_from_directory(os.path.join(BASE_DIR, 'timelapse'), filename)

# Fetch Files
@app.route('/update_files', methods=['GET'])
def update_files():
    files = make_tree(app.config['UPLOAD_PATH'])
    return files

def folder_size(path):
    """Bytes used by a folder and everything in it (0 if it does not exist)."""
    total = 0
    for folder, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    return total


# How much room is left, and what the web plotter itself uses
@app.route('/storage', methods=['GET'])
def storage():
    try:
        usage = shutil.disk_usage(app.config['UPLOAD_PATH'])
        disk = {'total': usage.total, 'used': usage.used, 'free': usage.free}
    except OSError:
        disk = {'total': None, 'used': None, 'free': None}
    try:
        history_bytes = os.path.getsize(history.DB_PATH)
    except OSError:
        history_bytes = 0
    return jsonify(dict(disk, uploads=folder_size(app.config['UPLOAD_PATH']), cache=folder_size('cache'),
                        history=history_bytes))


# Delete the uploaded files that have not been touched for a number of days
@app.route('/delete_old_files', methods=['POST'])
def delete_old_files():
    days = request.form.get('days', '')
    if not re.fullmatch('[0-9]{1,4}', days) or int(days) < 1:
        return 'Enter a number of days (1 or more)', 400
    if globals.queue_active or not plot_lock.acquire(blocking=False):
        return 'Files cannot be deleted while plotting', 409
    try:
        cutoff = time.time() - int(days) * 86400
        deleted = []
        for name in sorted(os.listdir(app.config['UPLOAD_PATH'])):
            path = upload_file_path(name)
            if not path or name == '.gitignore' or not os.path.isfile(path) or plot_queue.has_file(name):
                continue        # the files waiting in the queue are not "old": they are about to be plotted
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    deleted.append(name)
            except OSError:
                pass
    finally:
        plot_lock.release()
    if deleted:
        socketio.emit('status_log', {'data': 'Deleted {} file{} older than {} days'.format(
            len(deleted), '' if len(deleted) == 1 else 's', days)})
    return jsonify(deleted)


# Throw away what can be made again: analyses, previews and copies made for plotting
@app.route('/clear_cache', methods=['POST'])
def clear_cache():
    if globals.queue_active or not plot_lock.acquire(blocking=False):
        return 'The cache cannot be cleared while plotting', 409
    try:
        freed = folder_size('cache')
        shutil.rmtree('cache', ignore_errors=True)
    finally:
        plot_lock.release()
    return jsonify({'freed': freed})


# List COM Ports
@app.route('/update_ports', methods=['GET'])
def update_ports():
    ports = send2serial.listComPorts()
    # Keep the saved default selectable when its adapter is unplugged, or when it is a plain
    # /dev/ttyUSBx name that now has a stable /dev/serial/by-id/ alias
    saved = config_value('plotter', 'port')
    if saved and saved not in ports['content'] and PORT_RE.fullmatch(saved):
        ports['content'].append(saved)
    return ports

# What an HPGL file draws: size, pens, time estimate
@app.route('/analyze', methods=['GET'])
def analyze_file():
    name = request.args.get('file')
    path = upload_file_path(name)
    if not path or not path.lower().endswith('.hpgl') or not os.path.isfile(path):
        return 'Please select a valid .hpgl file', 400
    # While plotting only answer from the cache: a new analysis would compete with the serial loop
    if plot_lock.locked() and not hpgl_analysis.cached(path):
        return jsonify({'summary': None, 'busy': True})
    try:
        analysis = hpgl_analysis.analyze_cached(path)
    except OSError as e:
        return 'Could not read the file: ' + str(e), 500
    return jsonify({'summary': hpgl_analysis.summary(analysis, history.correction()), 'busy': False})


class ErrorCollector:
    """Stands in for socketio and keeps the error that send2serial reports, for the response."""

    def __init__(self):
        self.errors = []

    def emit(self, name, data=None, **kwargs):
        if name == 'error':
            self.errors.append(str(data.get('data')))


def plotter_commands(action, form):
    """The HPGL for a manual pen action, as (commands, query) or (None, error message)."""
    control = plotter_control
    if action == 'jog':
        dx, dy = control.parse_mm(form.get('dx', '')), control.parse_mm(form.get('dy', ''))
        if dx is None or dy is None:
            return None, 'Invalid distance'
        return control.jog(dx, dy), None
    if action == 'pen_up':
        return control.pen_up(), None
    if action == 'pen_down':
        return control.pen_down(), None
    if action == 'origin':
        return control.origin(), None
    if action == 'select_pen':
        pen = form.get('pen', '')
        commands = control.select_pen(int(pen)) if re.fullmatch(r'[0-8]', pen) else None
        return (commands, None) if commands else (None, 'Invalid pen')
    if action == 'position':
        return [], b'OA;'
    if action == 'bounds':
        path = upload_file_path(form.get('file'))
        if not path or not path.lower().endswith('.hpgl') or not os.path.isfile(path):
            return None, 'Please select a valid .hpgl file'
        analysis = hpgl_analysis.analyze_cached(path)
        if analysis is None or analysis['bounds'] is None:
            return None, 'The file draws nothing, or is too large, to trace its area'
        return control.trace_bounds(analysis['bounds'], draw=form.get('draw') == '1'), None
    return None, 'Unknown action'


# Move the pen by hand: jog, pen up/down, pick a pen, trace the plot area
@app.route('/plotter/<action>', methods=['POST'])
def plotter_action(action):
    port = request.form.get('port', '')
    if not PORT_RE.fullmatch(port):
        return 'Please select a valid COM port', 400
    baudrate = request.form.get('baudrate')
    if not valid_baudrate(baudrate):
        return 'Invalid baudrate', 400
    flowControl = request.form.get('flowControl')
    if flowControl not in FLOW_CONTROLS:
        return 'Invalid flow control', 400
    commands, query = plotter_commands(action, request.form)
    if commands is None:
        return query, 400

    # The serial port belongs to a plot while there is one
    if not plot_lock.acquire(blocking=False):
        return 'The plotter is busy', 409
    try:
        collector = ErrorCollector()
        try:
            reply = send2serial.run_commands(collector, port, int(baudrate), flowControl, commands, query)
        except send2serial.HPGLError as e:
            return 'The plotter did not answer: ' + str(e), 504
    finally:
        plot_lock.release()
    if reply is None:
        return 'Could not use the port: ' + ' '.join(collector.errors), 500
    if query is not None:
        position = plotter_control.parse_position(reply)
        if position is None:
            return 'Unexpected answer from the plotter', 502
        return jsonify(position)
    return 'OK'


# Recent plots, newest first
@app.route('/job_history', methods=['GET'])
def job_history():
    jobs = history.recent()
    for job in jobs:
        path = upload_file_path(job['file'])
        job['can_replot'] = bool(path and os.path.isfile(path))
        job['can_resume'] = can_resume(job)
        job.pop('options', None)
    return jsonify(jobs)

@app.route('/clear_history', methods=['POST'])
def clear_history():
    history.clear()
    return 'History cleared'

#auto detect baud
@app.route('/update_baud', methods=['POST'])
def update_baud():
    port = request.form.get('selected_port', '')
    if not PORT_RE.fullmatch(port):
        return 'Invalid port', 400
    if plot_lock.locked():
        return 'Cannot detect baudrate while plotting', 409
    baudrate = send2serial.getBaudRate(port)
    return str(baudrate)

# Delete uploaded filed
@app.route('/delete_file', methods=['POST'])
def delete_file():
    data = request.get_json(silent=True) or {}
    filename = data.get('filename')
    path = upload_file_path(filename)

    if plot_lock.locked() or globals.queue_active:
        return 'Files cannot be deleted while plotting', 409
    if plot_queue.has_file(filename):
        return 'The file is in the plot queue: remove it from the queue first', 409

    # Delete file
    if path and os.path.isfile(path):
        os.remove(path)
        socketio.emit('status_log', {'data': 'Deleted: ' + filename})
        return 'Deleted: ' + filename
    else:
        socketio.emit('error', {'data': 'The file does not exist'})
        return 'The file does not exist', 404

def plot_request(values):
    """Check the settings of a plot (the form of /start_plot, a stored job or a queue entry).

    Returns (request, None), or (None, (message, status)). The request holds the file's name and
    path, `options` (the settings as strings, for the history and the queue), the pens to plot
    and the analysis that was needed to pick them."""
    name = values.get('file')
    path = upload_file_path(name)
    if not path or not path.lower().endswith(PLOT_EXTENSIONS) or not os.path.isfile(path):
        return None, ('Please select a valid .hpgl or .cal file', 400)
    cal = send2serial.is_cal(path)

    port = values.get('port') or ''
    if not PORT_RE.fullmatch(port):
        return None, ('Please select a valid COM port', 400)
    baudrate = values.get('baudrate')
    if not valid_baudrate(baudrate):
        return None, ('Invalid baudrate', 400)
    flowControl = values.get('flowControl')
    if flowControl not in FLOW_CONTROLS:
        return None, ('Invalid flow control', 400)
    if cal and flowControl.upper() not in send2serial.CAL_FLOW_CONTROLS:
        return None, ('.cal files need XON/XOFF or no flow control', 400)

    pen_change = values.get('pen_change') or config_value('plotter', 'pen_change') or 'auto'
    if pen_change not in PEN_CHANGES:
        return None, ('Invalid pen change mode', 400)
    if cal:
        pen_change = 'auto'     # the pens are in the file (F1;...): there is nowhere to pause
    pens = None
    analysis = None
    pens_text = values.get('pens') or ''
    if pens_text and cal:
        return None, ('Pens cannot be picked in a .cal file', 400)
    if pens_text:
        if not PENS_RE.fullmatch(pens_text):
            return None, ('Invalid pens', 400)
        pens = sorted({int(pen) for pen in pens_text.split(',')})
        # Picking pens needs the file's pens: the analysis is cached after the preview
        analysis = hpgl_analysis.analyze_cached(path)
        if analysis is None:
            return None, ('This file is too large to pick pens from', 400)
        used = {segment['pen'] for segment in analysis['segments']}
        if not used.intersection(pens):
            return None, ('The file does not draw with the selected pens', 400)
        if used.issubset(pens):
            pens = None     # every pen is wanted: plot the file as it is
            analysis = None

    options = {'file': name, 'port': port, 'baudrate': str(baudrate), 'flowControl': flowControl,
               'tasmota': values.get('tasmota') or '', 'timelapse': values.get('timelapse') or '',
               'pens': pens_text, 'pen_change': pen_change}
    return {'name': name, 'path': path, 'options': options, 'pens': pens, 'analysis': analysis}, None


def claim_plot(name):
    """Take the plot lock for a new plot and forget the last one. False when a plot is running."""
    global current_plot
    if not plot_lock.acquire(blocking=False):
        return False
    current_plot = name
    globals.clear_wait()
    globals.stop_requested = False
    globals.plot_finished = False
    globals.reset_plot_state()
    return True


def begin_plot(values, resume_from=None, resume_job=None):
    """Start a plot in a background task (they run for a long time, so the request must not wait)."""
    if globals.queue_active:
        return 'The queue is running: Stop it first', 409
    request_, error = plot_request(values)
    if error:
        return error
    # Only one plot at a time
    if not claim_plot(request_['name']):
        return 'A plot is already running', 409
    options = request_['options']
    socketio.start_background_task(plot, request_['path'], options['port'], options['baudrate'],
                                   options['flowControl'], options['tasmota'], options['timelapse'],
                                   request_['pens'], options['pen_change'], request_['analysis'],
                                   options=options, resume_from=resume_from, resume_job=resume_job)
    return 'Plot started'


# Get Plotter settings from UI
@app.route('/start_plot', methods=['POST'])
def start_plot():
    return begin_plot(request.form)


def job_values(job):
    """The settings a stored plot was started with. Plots from before they were kept fall back to
    the columns every plot has."""
    values = dict(job.get('options') or {})
    values.setdefault('file', job['file'])
    values.setdefault('port', job['port'])
    values.setdefault('baudrate', str(job['baudrate']))
    values.setdefault('flowControl', job['flow_control'])
    return values


# Plot a file from the history again, with the settings it had
@app.route('/replot', methods=['POST'])
def replot():
    job = history.get(request.form.get('job'))
    if job is None:
        return 'No such plot', 404
    return begin_plot(job_values(job))


MAX_REWIND = 1024 * 1024


def can_resume(job):
    """Can this plot carry on? It was stopped or failed part way, and the file is still the one
    that was plotted."""
    if job['status'] not in ('stopped', 'failed') or not job.get('resume_offset') or send2serial.is_cal(job['file']):
        return False
    path = upload_file_path(job['file'])
    try:
        return bool(path) and os.path.getsize(path) == job.get('file_size')
    except OSError:
        return False


# Carry on a stopped or failed plot from where it got to. `rewind` goes back further (in bytes), because
# without buffer flow control nobody knows how much the plotter still had to draw.
@app.route('/resume_job', methods=['POST'])
def resume_job():
    job = history.get(request.form.get('job'))
    if job is None:
        return 'No such plot', 404
    if not can_resume(job):
        return 'This plot cannot be resumed (it was not stopped part way, or the file has changed)', 400
    rewind = request.form.get('rewind') or '0'
    if not re.fullmatch('[0-9]{1,7}', rewind) or int(rewind) > MAX_REWIND:
        return 'Invalid rewind', 400
    offset = max(job['resume_offset'] - int(rewind), 0)
    return begin_plot(job_values(job), resume_from=offset, resume_job=job['id'])

# Stop the printing process
@app.route('/stop_plot', methods=['POST'])
def stop_plot():
    if globals.queue_active:
        globals.queue_hold = True       # whichever plot is running, the queue does not go on
    if not plot_lock.locked():
        # Make sure the UI is not left locked
        socketio.emit('lock_edit', {'data': 'off'})
        return 'The queue will stop' if globals.queue_active else 'No plot is running'

    globals.stop_requested = True
    globals.printing = False
    globals.clear_wait()     # a paused plot must wake up to notice the stop
    if globals.plot_finished:
        # Only waiting (to switch the plotter off, or for the paper): the plot itself was not cancelled
        return 'Skipping the wait'
    plotter_name = config.get('plotter', 'name', fallback='Plotter')
    notification.send('finish', '{}: {}: Cancelled'.format(plotter_name, current_plot), file=current_plot)
    globals.current_file = 'None'
    return 'Plot stopped'

def set_paused(paused):
    if not plot_lock.locked():
        return 'No plot is running', 409
    if not paused and globals.paused and globals.wait_reason == 'disconnected':
        return 'The plotter is not connected yet', 409
    if globals.paused != paused:
        if paused:
            globals.paused = True
        else:
            globals.clear_wait()
        PlotEvents().emit('status_log', {'data': (
            'Plot paused. The plotter finishes what is already in its buffer.' if paused
            else 'Plot resumed.')})
        broadcast_plot_state()
    return 'Plot paused' if paused else 'Plot resumed'

# Hold back the data sent to the plotter, and continue again
@app.route('/pause_plot', methods=['POST'])
def pause_plot():
    return set_paused(True)

@app.route('/resume_plot', methods=['POST'])
def resume_plot():
    return set_paused(False)

# ////////////////////////////////////////////////////////////////////////////
# Read-only status for other programs (Home Assistant, a script). Behind the same login as the page.
def api_state(plot):
    """One word for what the plotter is doing."""
    if plot['running']:
        if plot['paused']:
            return plot['wait_reason'] or 'paused'
        return 'plotting'
    return 'plotting' if plot['queue_active'] else 'idle'      # between two files of the queue


@app.route('/api/status', methods=['GET'])
def api_status():
    plot = plot_state()
    queue = queue_state()
    last = history.recent(1)
    return jsonify({
        'plotter': config_value('plotter', 'name') or 'Plotter',
        'state': api_state(plot),
        'plot': plot,
        'queue': {'active': queue['active'], 'message': queue['message'],
                  'waiting': sum(1 for item in queue['items'] if item['status'] == 'waiting'),
                  'items': [item['file'] for item in queue['items']]},
        'last_plot': {key: last[0][key] for key in ('id', 'file', 'status', 'started_at', 'finished_at', 'progress')} if last else None,
    })


# ////////////////////////////////////////////////////////////////////////////
# The plot queue: several files in a row. Stop holds the queue (the plot that was running stays at
# the top, waiting), it does not clear it.
queue_lock = threading.Lock()


def queue_state():
    return {'active': globals.queue_active, 'message': globals.queue_message, 'items': [
        {'id': item['id'], 'file': item['file'], 'status': item['status'], 'pause_after': item['pause_after'],
         'pens': item['options'].get('pens', ''), 'tasmota': item['options'].get('tasmota') == 'on'}
        for item in plot_queue.items()]}


def broadcast_queue():
    socketio.emit('queue_state', {'data': queue_state()})


def run_queue():
    """Plot the queued files one after another. Runs in a background task."""
    # Whether the previous plot left the plotter switched on: then this one need not switch it on
    plotter = {'left_on': False}
    message = ''
    try:
        while True:
            item = plot_queue.next_waiting()
            if item is None:
                message = 'The queue is finished'
                break
            if globals.queue_hold:
                message = 'The queue is stopped'
                break
            request_, error = plot_request(item['options'])
            if error:
                message = 'The queue is held: {}: {}'.format(item['file'], error[0])
                PlotEvents().emit('error', {'data': message})
                break
            # Jogging takes the plotter for a moment: give it a few seconds
            claimed = False
            for _ in range(20):
                claimed = claim_plot(request_['name'])
                if claimed or globals.queue_hold:
                    break
                time.sleep(0.25)
            if not claimed:
                message = 'The queue is stopped' if globals.queue_hold else 'The queue is held: the plotter is busy'
                break
            plot_queue.set_status(item['id'], plot_queue.RUNNING)
            broadcast_queue()

            def another_follows(item=item):
                plotter['left_on'] = plot_queue.count_waiting(exclude=item['id']) > 0
                return plotter['left_on']

            options = request_['options']
            already_on = plotter['left_on']
            plotter['left_on'] = False
            outcome = plot(request_['path'], options['port'], options['baudrate'], options['flowControl'],
                           options['tasmota'], options['timelapse'], request_['pens'], options['pen_change'],
                           request_['analysis'], options=options, power_on=not already_on,
                           keep_power=another_follows, paper_change=item['pause_after'])
            if outcome != 'completed':
                plotter['left_on'] = False
            if outcome == 'completed':
                plot_queue.done(item['id'])
            else:
                plot_queue.set_status(item['id'], plot_queue.WAITING)     # still to do: it stays at the top
            broadcast_queue()
            if outcome != 'completed' or globals.queue_hold:
                message = 'The queue is stopped' if globals.queue_hold else 'The queue is held: {} {}'.format(
                    item['file'], 'failed' if outcome == 'failed' else 'was stopped')
                break
    except Exception as e:
        traceback.print_exc()
        message = 'The queue stopped: ' + repr(e)
        PlotEvents().emit('error', {'data': message})
    finally:
        globals.queue_active = False
        globals.queue_message = message
        PlotEvents().emit('status_log', {'data': message})
        broadcast_queue()
        broadcast_plot_state()


@app.route('/queue', methods=['GET'])
def get_queue():
    return jsonify(queue_state())


@app.route('/queue/add', methods=['POST'])
def queue_add():
    request_, error = plot_request(request.form)
    if error:
        return error
    pause_after = request.form.get('pause_after') in ('1', 'true', 'on')
    if plot_queue.add(request_['options'], pause_after) is None:
        return 'The queue is full or cannot be saved (at most {} files)'.format(plot_queue.MAX_ITEMS), 400
    broadcast_queue()
    return 'Added to the queue'


@app.route('/queue/remove', methods=['POST'])
def queue_remove():
    if not plot_queue.remove(request.form.get('id')):
        return 'That plot is not waiting in the queue', 404
    broadcast_queue()
    return 'Removed'


@app.route('/queue/move', methods=['POST'])
def queue_move():
    direction = request.form.get('direction')
    if direction not in ('up', 'down'):
        return 'Invalid direction', 400
    if not plot_queue.move(request.form.get('id'), -1 if direction == 'up' else 1):
        return 'It cannot move that way', 409
    broadcast_queue()
    return 'Moved'


@app.route('/queue/pause_after', methods=['POST'])
def queue_pause_after():
    item = plot_queue.get(request.form.get('id'))
    if item is None:
        return 'No such plot in the queue', 404
    plot_queue.set_pause_after(item['id'], request.form.get('value') in ('1', 'true', 'on'))
    broadcast_queue()
    return 'OK'


@app.route('/queue/clear', methods=['POST'])
def queue_clear():
    plot_queue.clear()
    broadcast_queue()
    return 'Queue cleared'


@app.route('/queue/start', methods=['POST'])
def queue_start():
    with queue_lock:
        if globals.queue_active:
            return 'The queue is already running', 409
        if plot_lock.locked():
            return 'A plot is already running', 409
        if plot_queue.next_waiting() is None:
            return 'The queue is empty', 400
        globals.queue_active = True
        globals.queue_hold = False
        globals.queue_message = ''
    broadcast_queue()
    socketio.start_background_task(run_queue)
    return 'Queue started'


# The vpype plugins that are installed, for the custom command box in the convert dialog
@app.route('/vpype_plugins', methods=['GET'])
def list_vpype_plugins():
    return jsonify({'plugins': vpype_plugins.installed(), 'install': vpype_plugins.install_command()})


# Saved sets of conversion options
@app.route('/presets', methods=['GET'])
def list_presets():
    return jsonify(presets.all())


@app.route('/presets', methods=['POST'])
def save_preset():
    name = (request.form.get('name') or '').strip()
    options, error = conversion_options(request.form)
    if error:
        return error, 400
    error = presets.save(name, preset_form(options))
    if error:
        return error, 400
    return 'Saved preset ' + name


@app.route('/presets/delete', methods=['POST'])
def delete_preset():
    name = request.form.get('name') or ''
    if not presets.delete(name):
        return 'No such preset', 404
    return 'Deleted preset ' + name


# Start converting file using vpype
@app.route('/start_conversion', methods=['POST'])
def start_conversion():
    name = request.form.get('file')
    file = upload_file_path(name)
    if not file or not file.lower().endswith('.svg') or not os.path.isfile(file):
        return 'Please select a valid .svg file', 400

    options, error = conversion_options(request.form)
    if error:
        return error, 400

    try:
        output = run_conversion(file, options)
    except (Exception, SystemExit) as e:
        traceback.print_exc()
        socketio.emit('error', {'data': 'Conversion failed: ' + repr(e)})
        return 'File not converted.'

    return output

def remove_old_previews():
    """Previews that were never saved are only kept for a day."""
    try:
        names = os.listdir(PREVIEW_DIR)
    except OSError:
        return
    for name in names:
        path = os.path.join(PREVIEW_DIR, name)
        try:
            if time.time() - os.path.getmtime(path) > PREVIEW_MAX_AGE:
                os.remove(path)
        except OSError:
            pass


def preview_path(name):
    """The path of a previewed conversion, or None if `name` is not one."""
    if not isinstance(name, str) or not HPGL_NAME_RE.fullmatch(name):
        return None
    path = os.path.join(PREVIEW_DIR, name)
    return path if os.path.isfile(path) else None


# Convert into the preview folder (not uploads/) so the result can be looked at before it is kept
@app.route('/preview_conversion', methods=['POST'])
def preview_conversion():
    name = request.form.get('file')
    file = upload_file_path(name)
    if not file or not file.lower().endswith('.svg') or not os.path.isfile(file):
        return 'Please select a valid .svg file', 400
    options, error = conversion_options(request.form)
    if error:
        return error, 400

    os.makedirs(PREVIEW_DIR, exist_ok=True)
    remove_old_previews()
    result_name = os.path.basename(output_name(
        file, options['outputsize'], options['pageorientation'], options['device'], options['custom_comand'],
        options['linemerge'], options['linesort'], options['linesimplify'], options['reloop'],
        options['margin'], options['rotate'], options['mirror_x'], options['mirror_y']))
    path = os.path.join(PREVIEW_DIR, result_name)
    try:
        if os.path.exists(path):
            os.remove(path)
        run_conversion(file, options, output=path)
    except (Exception, SystemExit) as e:
        traceback.print_exc()
        socketio.emit('error', {'data': 'Conversion failed: ' + repr(e)})
    if not os.path.isfile(path):
        return 'File not converted.', 422

    try:
        summary = hpgl_analysis.summary(hpgl_analysis.analyze_cached(path), history.correction())
    except OSError:
        summary = None
    return jsonify({'name': result_name, 'summary': summary})


@app.route('/preview_files/<name>')
def preview_file(name):
    if preview_path(name) is None:
        return 'No such preview', 404
    response = send_from_directory(os.path.join(BASE_DIR, PREVIEW_DIR), name)
    response.headers['Cache-Control'] = 'no-store'
    return response


# Keep a previewed conversion: it moves into uploads/ under the name it would have got
@app.route('/save_preview', methods=['POST'])
def save_preview():
    name = request.form.get('name')
    path = preview_path(name)
    if path is None:
        return 'That preview is no longer available. Preview it again.', 404
    if plot_lock.locked() and name == current_plot:
        return 'This file is currently being plotted', 409
    target = os.path.join(app.config['UPLOAD_PATH'], name)
    try:
        os.replace(path, target)
    except OSError as e:
        return 'Could not save the file: ' + str(e), 500
    socketio.emit('status_log', {'data': 'File converted.'})
    return 'Exported ' + app.config['UPLOAD_PATH'] + '/' + name


# Make a drawing of some typed text, to be converted like any svg
@app.route('/create_text', methods=['POST'])
def create_text_drawing():
    form = request.form
    try:
        text = text_drawing.clean_text(form.get('text'))
    except text_drawing.TextError as e:
        return str(e), 400
    font, align = form.get('font'), form.get('align')
    page, orientation = form.get('outputsize'), form.get('pageorientation')
    size, margin = form.get('size') or '', form.get('margin') or '15'
    if font not in text_drawing.TEXT_FONTS:
        return 'Invalid font', 400
    if align not in text_drawing.TEXT_ALIGNMENTS:
        return 'Invalid alignment', 400
    if page not in OUTPUT_SIZES:
        return 'Invalid page size', 400
    if orientation not in ORIENTATIONS:
        return 'Invalid page orientation', 400
    if not TEXT_SIZE_RE.fullmatch(size) or not TEXT_SIZE_MM[0] <= float(size) <= TEXT_SIZE_MM[1]:
        return 'Invalid text size ({} to {} mm)'.format(*TEXT_SIZE_MM), 400
    if not MARGIN_RE.fullmatch(margin) or float(margin) > MAX_MARGIN_MM:
        return 'Invalid margin (0 to {} mm)'.format(MAX_MARGIN_MM), 400

    name = text_drawing.file_name(text)
    path = upload_file_path(name)
    if path is None:
        return 'Invalid file name', 400
    try:
        make_text_svg(text, font, float(size), page, orientation == 'landscape', float(margin), align, output=path)
    except text_drawing.TextError as e:
        return str(e), 400
    except (Exception, SystemExit) as e:
        traceback.print_exc()
        return 'Could not create the text: ' + str(e), 500
    return 'Created ' + name


def power_action(command):
    """Run reboot/poweroff through passwordless sudo once the response has been sent.

    A user without NOPASSWD sudo would otherwise get "Rebooting now" and nothing would happen,
    so check up front and say what is missing."""
    try:
        allowed = subprocess.run(['sudo', '-n', '-l', command], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        allowed = False
    if not allowed:
        return ('Cannot %s: the user running the web plotter needs passwordless sudo for "%s" '
                '(add a NOPASSWD sudoers rule for it).' % (command, command)), 500

    response = Response('action_%s started' % command)

    @response.call_on_close
    def on_close():
        try:
            subprocess.Popen(['sudo', '-n', command])
        except OSError:
            traceback.print_exc()

    return response

# Start reboot sequence
@app.route('/action_reboot', methods=['POST'])
def action_reboot():
    return power_action('reboot')

# Start poweroff sequence
@app.route('/action_poweroff', methods=['POST'])
def action_poweroff():
    return power_action('poweroff')

# Toggle tasmota switch
@app.route('/action_tasmota', methods=['POST'])
def action_tasmota():
    if config.get('tasmota', 'tasmota_enable', fallback='false').lower() != 'true':
        return 'Tasmota is not enabled', 409
    if tasmota.tasmota_setToggle(socketio) is False:
        return 'Could not contact the Tasmota device', 502
    return 'action_tasmota started'


# Settings that can be changed from the UI: form field -> (section, option, validator)
def _is_bool(value):
    return value in ('true', 'false')

def _is_seconds(value):
    return re.fullmatch(r'\d{1,3}', value) is not None and int(value) <= 600

def _is_text(value):
    return len(value) <= 200 and not CONTROL_CHARS_RE.search(value)

CONFIG_FIELDS = {
    'telegram_token': ('telegram', 'telegram_token', lambda v: re.fullmatch(r'[A-Za-z0-9:_-]*', v) is not None),
    'telegram_chatid': ('telegram', 'telegram_chatid', lambda v: re.fullmatch(r'[@\w-]*', v) is not None),
    'tasmota_enable': ('tasmota', 'tasmota_enable', _is_bool),
    'tasmota_ip': ('tasmota', 'tasmota_ip', lambda v: HOST_RE.fullmatch(v) is not None),
    'tasmota_on_delay': ('tasmota', 'tasmota_on_delay', _is_seconds),
    'tasmota_off_delay': ('tasmota', 'tasmota_off_delay', _is_seconds),
    'timelapse_enable': ('timelapse', 'timelapse_enable', _is_bool),
    'timelapse_auto_start': ('timelapse', 'timelapse_auto_start', _is_bool),
    'timelapse_preview': ('timelapse', 'timelapse_preview', _is_bool),
    'notify_start': ('notifications', 'notify_start', _is_bool),
    'notify_finish': ('notifications', 'notify_finish', _is_bool),
    'notify_error': ('notifications', 'notify_error', _is_bool),
    'notify_pen_change': ('notifications', 'notify_pen_change', _is_bool),
    'notify_progress_every': ('notifications', 'notify_progress_every', lambda v: re.fullmatch(r'[0-9]{1,2}', v) is not None and int(v) <= 50),
    'webhook_url': ('notifications', 'webhook_url', lambda v: v == '' or (len(v) <= 500 and notification.URL_RE.fullmatch(v) is not None)),
    'mqtt_host': ('notifications', 'mqtt_host', lambda v: v == '' or notification.HOST_RE.fullmatch(v) is not None),
    'mqtt_port': ('notifications', 'mqtt_port', lambda v: v == '' or (re.fullmatch(r'[0-9]{1,5}', v) is not None and 0 < int(v) < 65536)),
    'mqtt_topic': ('notifications', 'mqtt_topic', lambda v: v == '' or notification.TOPIC_RE.fullmatch(v) is not None),
    'mqtt_username': ('notifications', 'mqtt_username', _is_text),
    'mqtt_password': ('notifications', 'mqtt_password', _is_text),
    'plotter_name': ('plotter', 'name', _is_text),
    'plotter_port': ('plotter', 'port', lambda v: v == '' or PORT_RE.fullmatch(v) is not None),
    'plotter_device': ('plotter', 'device', lambda v: v in DEVICES),
    'plotter_baudrate': ('plotter', 'baudrate', valid_baudrate),
    'plotter_flowControl': ('plotter', 'flowControl', lambda v: v in FLOW_CONTROLS),
    'plotter_pen_change': ('plotter', 'pen_change', lambda v: v in PEN_CHANGES),
    # Basic auth needs both; HTTP basic auth cannot have a ':' in the user name
    'auth_username': ('auth', 'username', lambda v: _is_text(v) and ':' not in v),
    'auth_password': ('auth', 'password', _is_text),
}

# Shown when an older config.ini does not have the setting yet
CONFIG_DEFAULTS = {'tasmota_on_delay': '2', 'tasmota_off_delay': '30', 'notify_start': 'true',
                   'notify_finish': 'true', 'notify_error': 'true', 'notify_pen_change': 'true',
                   'notify_progress_every': '0', 'mqtt_port': '1883', 'mqtt_topic': 'webplotter'}

# Never sent back to the browser: an empty password in a save means "keep the current one"
WRITE_ONLY_FIELDS = {'auth_password', 'mqtt_password'}

def store_config(updates):
    """Set {(section, option): value} in the live config and write config.ini. The caller holds
    config_lock."""
    for (section, option), value in updates.items():
        if not config.has_section(section):
            config.add_section(section)
        # '%' must be escaped because of configparser interpolation
        config[section][option] = value.replace('%', '%%')

    # Write atomically so a crash can't leave a truncated config.ini
    with open('config.ini.tmp', 'w') as configfile:
        config.write(configfile)
    try:
        os.chmod('config.ini.tmp', 0o600)
    except OSError:
        pass
    os.replace('config.ini.tmp', 'config.ini')


# Update configfile values
@app.route('/save_configfile', methods=['GET', 'POST'])
def save_configfile():
    if request.method == "POST":
        updates = {}
        for field, (section, option, is_valid) in CONFIG_FIELDS.items():
            if field in request.form:
                # Passwords may start or end with a space
                value = request.form.get(field, '')
                if field not in WRITE_ONLY_FIELDS:
                    value = value.strip()
                if not is_valid(value):
                    return 'Invalid value for {}'.format(field), 400
                if field in WRITE_ONLY_FIELDS and value == '':
                    continue
                updates[(section, option)] = value

        with config_lock:
            if 'auth_username' in request.form:
                username = updates.get(('auth', 'username'), '')
                has_password = (('auth', 'password') in updates
                                or bool(config_value('auth', 'password')))
                if username and not has_password:
                    return 'Enter a password to go with the login name', 400
                if not username:
                    # Clearing the login name switches the login off
                    updates.pop(('auth', 'username'), None)
                    updates.pop(('auth', 'password'), None)
                    if config.has_section('auth'):
                        config.remove_section('auth')

            store_config(updates)

        return 'Configuration Updated'

    output = {field: config_value(section, option) or CONFIG_DEFAULTS.get(field, '')
              for field, (section, option, _) in CONFIG_FIELDS.items()
              if field not in WRITE_ONLY_FIELDS}
    output['auth_password_set'] = bool(config_value('auth', 'password'))
    output['mqtt_password_set'] = bool(config_value('notifications', 'mqtt_password'))
    return jsonify(output)

# ////////////////////////////////////////////////////////////////////////////
# Backup and restore

# A zip of the settings, the history (with the presets and the queue) and, if asked for, the uploads.
# It holds the passwords that are in config.ini: keep it somewhere private.
@app.route('/backup', methods=['GET'])
def download_backup():
    handle, path = tempfile.mkstemp(suffix='.zip')
    os.close(handle)
    try:
        uploads = app.config['UPLOAD_PATH'] if request.args.get('uploads') == '1' else None
        backup.create(path, 'config.ini', history.database, uploads)
        size = os.path.getsize(path)
    except Exception:
        os.remove(path)
        raise

    def stream():
        # Removing the file here, not in a close callback: this runs when the download ends or is dropped
        try:
            with open(path, 'rb') as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    return Response(stream(), mimetype='application/zip', headers={
        'Content-Length': str(size),
        'Content-Disposition': time.strftime('attachment; filename="webplotter-backup-%Y%m%d-%H%M.zip"')})


def config_from_backup(archive):
    """The settings of a backup as {(section, option): value}, each checked like a save from the
    page. Settings that are not in CONFIG_FIELDS are ignored. Raises BackupError."""
    parser = backup.read_config(archive)
    updates = {}
    for field, (section, option, is_valid) in CONFIG_FIELDS.items():
        if not parser.has_option(section, option):
            continue
        try:
            value = parser.get(section, option)
        except configparser.InterpolationError:
            value = parser.get(section, option, raw=True)
        if field not in WRITE_ONLY_FIELDS:
            value = value.strip()
        if field in WRITE_ONLY_FIELDS and value == '':
            continue
        if not is_valid(value):
            raise backup.BackupError('The backup has an invalid value for {}'.format(field))
        updates[(section, option)] = value
    # A login needs both parts. A backup without one does not switch the current one off.
    if not (updates.get(('auth', 'username')) and updates.get(('auth', 'password'))):
        updates.pop(('auth', 'username'), None)
        updates.pop(('auth', 'password'), None)
    return updates


# Put a backup back. Everything is checked first; then the settings, the history and the files
# that are in the zip replace the current ones (files with other names are kept).
@app.route('/restore', methods=['POST'])
def restore_backup():
    upload = request.files.get('backup')
    if upload is None:
        return 'No file received', 400
    if globals.queue_active or not plot_lock.acquire(blocking=False):
        return 'A backup cannot be restored while plotting', 409
    folder = tempfile.mkdtemp()
    try:
        zip_path = os.path.join(folder, 'backup.zip')
        upload.save(zip_path)
        try:
            archive = zipfile.ZipFile(zip_path)
        except zipfile.BadZipFile:
            return 'That is not a zip file', 400
        with archive:
            try:
                members = backup.inspect(archive)
                updates = config_from_backup(archive) if backup.CONFIG in members else {}
                database = backup.extract_database(archive, folder) if backup.DATABASE in members else None
            except backup.BackupError as e:
                return str(e), 400

            restored = {'config': len(updates), 'history': False, 'uploads': 0}
            if updates:
                with config_lock:
                    store_config(updates)
            if database:
                source = sqlite3.connect(database)
                try:
                    with history.database() as conn:
                        source.backup(conn)
                finally:
                    source.close()
                # A backup from an older version may lack newer columns and tables
                history.init()
                plot_queue.init()
                presets.init()
                restored['history'] = True
            for name in sorted(members):
                match = backup.UPLOAD_NAME_RE.fullmatch(name)
                path = match and upload_file_path(match.group(1))
                if path:
                    backup.extract_upload(archive, name, path)
                    restored['uploads'] += 1
        socketio.emit('status_log', {'data': 'Restored a backup: {} settings, {}, {} files'.format(
            restored['config'], 'the history' if restored['history'] else 'no history', restored['uploads'])})
        broadcast_queue()
        return jsonify(restored)
    except (OSError, sqlite3.Error) as e:
        traceback.print_exc()
        return 'The backup could not be restored: ' + type(e).__name__, 500
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        plot_lock.release()


# Send a test message to every notification channel that is set up
@app.route('/action_test_notification', methods=['POST'])
def action_test_notification():
    if not notification.channels():
        return 'No notification channel is set up (save a Telegram token and chat id, a webhook URL or an MQTT host first)', 400
    results = notification.send_test()
    text = ', '.join('{}: {}'.format(channel, 'sent' if reason is None else 'failed (' + reason + ')')
                     for channel, reason in results.items())
    return (text, 200) if all(reason is None for reason in results.values()) else (text, 502)

# On connection
@socketio.on('connect')
def on_connect(auth=None):
    # Socket.IO connections bypass before_request, so check access here as well
    if not is_authorized():
        return False
    # Sync the UI with the real state (e.g. after a page refresh during a plot, or when another
    # device opens the page): lock, file being plotted, progress, pause state and the log so far
    emit('lock_edit', {'data': 'on' if plot_lock.locked() else 'off'})
    state = plot_state()
    state['log'] = globals.plot_log_lines()
    emit('plot_state', {'data': state})
    emit('queue_state', {'data': queue_state()})

@socketio.event
def connection(message):
    print('Client connected')

if __name__ == "__main__":
    # use_reloader is off even in debug mode: it would restart the server mid-plot
    socketio.run(app, host='0.0.0.0', port=5000, debug=DEBUG, use_reloader=False, allow_unsafe_werkzeug=True)
