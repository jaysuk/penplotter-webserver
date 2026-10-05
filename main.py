import configparser
import hmac
import os
import re
import secrets
import subprocess
import threading
import time
import traceback
from urllib.parse import urlparse

# The compiled modules (config, send2serial, convert_vpype) use paths relative to
# the application directory, so always run from there.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

from flask import Flask, Response, render_template, request, send_from_directory, jsonify
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO, emit

import globals
import history
import hpgl_analysis
import notification
import send2serial
import tasmota
from convert_vpype import convert_file
from config import config
# import RPi.GPIO as GPIO

globals.initialize()
history.init()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024
app.config['UPLOAD_EXTENSIONS'] = ['.svg', '.hpgl']
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
# Analysing a bigger file takes long enough on a Pi to be worth a line in the log
SLOW_ANALYSIS_BYTES = 1024 * 1024

for _section in ('telegram', 'tasmota', 'timelapse', 'plotter'):
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
                    tree['content'].append(dict(name=name))
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
    return isinstance(value, str) and value.isdigit() and 300 <= int(value) <= 921600


def check_vpype_command(command):
    """Return an error message if a custom vpype command line is not allowed, else None."""
    if not command:
        return None
    if len(command) > 200:
        return 'Custom vpype command is too long'
    if not CUSTOM_COMMAND_RE.fullmatch(command):
        return 'Custom vpype commands may only contain letters, numbers, spaces and . _ = + -'
    for token in command.split():
        if token.lstrip('-').lower() in BLOCKED_VPYPE_COMMANDS:
            return 'The vpype command "{}" is not allowed'.format(token)
    return None


class PlotEvents:
    """Stands in for socketio during a plot: sends each event on and remembers it, so a page that
    is refreshed (or opened on another device) mid-plot can be brought up to date."""

    def emit(self, name, data=None, **kwargs):
        globals.record_event(name, data)
        socketio.emit(name, data, **kwargs)
        if name == 'pen_change':
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


def plot(file, port, baudrate, flowControl, poweroff, timelapse, pens=None, pen_change='auto', analysis=None):
    """Run a plot. Runs in a background task; the caller must already hold plot_lock."""
    global current_plot
    events = PlotEvents()
    job = history.start(os.path.basename(file), port, baudrate, flowControl)
    outcome, error = None, None
    temporary = None
    try:
        # Lock editing while printing
        socketio.emit('lock_edit', {'data': 'on'})
        broadcast_plot_state()

        # Work out how long the plot takes (and which pens it uses) before anything is switched
        # on. A file that cannot be analysed still plots, just without a time left or pen change
        # pauses.
        send_path = file
        try:
            send_path, analysis, temporary = prepare_plot_file(events, file, analysis, pens)
        except (OSError, ValueError) as e:
            raise ValueError('Could not prepare the plot: ' + str(e))
        if analysis is None and pen_change == 'pause':
            events.emit('status_log', {'data': 'The file is too large to analyse: no time left, no pen change pauses.'})
        if analysis is not None:
            history.set_estimate(job, analysis['seconds'])

        # Tasmota - switch the plotter on and give it time to start up
        if poweroff == 'on':
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

        # Tasmota - turn the plotter off, once it has had time to finish drawing. Flow control
        # without buffer feedback can have a lot of the plot still queued in the plotter when the
        # last byte is sent, so this is a delay you set (Stop skips it).
        if poweroff == 'on':
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
                       drawn_s=globals.drawn_seconds if outcome == 'completed' else None)
        if temporary:
            try:
                os.remove(temporary)
            except OSError:
                pass
        globals.printing = False
        globals.clear_wait()
        globals.current_file = 'None'
        current_plot = None
        plot_lock.release()
        # Unlock editing
        socketio.emit('lock_edit', {'data': 'off'})
        broadcast_plot_state()


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

@app.route('/')
def index():
    files = make_tree(app.config['UPLOAD_PATH'])
    return render_template('index.html', files=files)


# Upload
@app.route('/', methods=['POST'])
def upload_files():
    uploaded_file = request.files.get('file')
    if uploaded_file is None:
        return 'No file received', 400
    filename = secure_filename(uploaded_file.filename or '')
    base, ext = os.path.splitext(filename)
    ext = ext.lower()
    if not base or ext not in app.config['UPLOAD_EXTENSIONS']:
        return 'Only .svg and .hpgl files are accepted', 400
    filename = base + ext
    if plot_lock.locked() and filename == current_plot:
        return 'This file is currently being plotted', 409
    uploaded_file.save(os.path.join(app.config['UPLOAD_PATH'], filename))
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


# Recent plots, newest first
@app.route('/job_history', methods=['GET'])
def job_history():
    return jsonify(history.recent())

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

    if plot_lock.locked():
        return 'Files cannot be deleted while plotting', 409

    # Delete file
    if path and os.path.isfile(path):
        os.remove(path)
        socketio.emit('status_log', {'data': 'Deleted: ' + filename})
        return 'Deleted: ' + filename
    else:
        socketio.emit('error', {'data': 'The file does not exist'})
        return 'The file does not exist', 404

# Get Plotter settings from UI
@app.route('/start_plot', methods=['POST'])
def start_plot():
    global current_plot
    name = request.form.get('file')
    path = upload_file_path(name)
    if not path or not path.lower().endswith('.hpgl') or not os.path.isfile(path):
        return 'Please select a valid .hpgl file', 400

    port = request.form.get('port', '')
    if not PORT_RE.fullmatch(port):
        return 'Please select a valid COM port', 400
    baudrate = request.form.get('baudrate')
    if not valid_baudrate(baudrate):
        return 'Invalid baudrate', 400
    flowControl = request.form.get('flowControl')
    if flowControl not in FLOW_CONTROLS:
        return 'Invalid flow control', 400
    poweroff = request.form.get('tasmota')
    timelapse = request.form.get('timelapse')

    pen_change = request.form.get('pen_change') or config_value('plotter', 'pen_change') or 'auto'
    if pen_change not in PEN_CHANGES:
        return 'Invalid pen change mode', 400
    pens = None
    analysis = None
    pens_text = request.form.get('pens') or ''
    if pens_text:
        if not PENS_RE.fullmatch(pens_text):
            return 'Invalid pens', 400
        pens = sorted({int(pen) for pen in pens_text.split(',')})
        # Picking pens needs the file's pens: the analysis is cached after the preview
        analysis = hpgl_analysis.analyze_cached(path)
        if analysis is None:
            return 'This file is too large to pick pens from', 400
        used = {segment['pen'] for segment in analysis['segments']}
        if not used.intersection(pens):
            return 'The file does not draw with the selected pens', 400
        if used.issubset(pens):
            pens = None     # every pen is wanted: plot the file as it is
            analysis = None

    # Only one plot at a time
    if not plot_lock.acquire(blocking=False):
        return 'A plot is already running', 409
    current_plot = name
    globals.clear_wait()
    globals.stop_requested = False
    globals.plot_finished = False
    globals.reset_plot_state()

    # Plots run for a long time, so don't block the request
    socketio.start_background_task(plot, path, port, baudrate, flowControl, poweroff, timelapse,
                                   pens, pen_change, analysis)

    return 'Plot started'

# Stop the printing process
@app.route('/stop_plot', methods=['POST'])
def stop_plot():
    if not plot_lock.locked():
        # Make sure the UI is not left locked
        socketio.emit('lock_edit', {'data': 'off'})
        return 'No plot is running'

    globals.stop_requested = True
    globals.printing = False
    globals.clear_wait()     # a paused plot must wake up to notice the stop
    if globals.plot_finished:
        # Only waiting to switch the plotter off: the plot itself was not cancelled
        return 'Skipping the wait'
    plotter_name = config.get('plotter', 'name', fallback='Plotter')
    socketio.start_background_task(
        notification.telegram_sendNotification,
        '{}: {}: Cancelled'.format(plotter_name, current_plot))
    globals.current_file = 'None'
    return 'Plot stopped'

def set_paused(paused):
    if not plot_lock.locked():
        return 'No plot is running', 409
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

# Start converting file using vpype
@app.route('/start_conversion', methods=['POST'])
def start_conversion():
    name = request.form.get('file')
    file = upload_file_path(name)
    if not file or not file.lower().endswith('.svg') or not os.path.isfile(file):
        return 'Please select a valid .svg file', 400

    outputsize = request.form.get('outputsize')
    pageorientation = request.form.get('pageorientation')
    device = request.form.get('device')
    speed = request.form.get('speed') or ''
    linemerge = request.form.get('linemerge')
    linesort = request.form.get('linesort')
    linesimplify = request.form.get('linesimplify')
    reloop = request.form.get('reloop')
    custom_comand = request.form.get('command_input')

    if outputsize not in OUTPUT_SIZES:
        return 'Invalid output size', 400
    if pageorientation not in ORIENTATIONS:
        return 'Invalid page orientation', 400
    if device not in DEVICES:
        return 'Invalid plotter device', 400
    if not SPEED_RE.fullmatch(speed):
        return 'Invalid plot speed', 400
    error = check_vpype_command(custom_comand)
    if error:
        return error, 400

    try:
        output = convert_file(file, outputsize, pageorientation, device, speed, custom_comand, linemerge, linesort, linesimplify, reloop, socketio)
    except (Exception, SystemExit) as e:
        traceback.print_exc()
        socketio.emit('error', {'data': 'Conversion failed: ' + repr(e)})
        return 'File not converted.'

    return output

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
CONFIG_DEFAULTS = {'tasmota_on_delay': '2', 'tasmota_off_delay': '30'}

# Never sent back to the browser: an empty password in a save means "keep the current one"
WRITE_ONLY_FIELDS = {'auth_password'}

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
                if field == 'auth_password' and value == '':
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

        return 'Configuration Updated'

    output = {field: config_value(section, option) or CONFIG_DEFAULTS.get(field, '')
              for field, (section, option, _) in CONFIG_FIELDS.items()
              if field not in WRITE_ONLY_FIELDS}
    output['auth_password_set'] = bool(config_value('auth', 'password'))
    return jsonify(output)

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

@socketio.event
def connection(message):
    print('Client connected')

if __name__ == "__main__":
    # use_reloader is off even in debug mode: it would restart the server mid-plot
    socketio.run(app, host='0.0.0.0', port=5000, debug=DEBUG, use_reloader=False, allow_unsafe_werkzeug=True)
