import hmac
import os
import re
import secrets
import shlex
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
import notification
import send2serial
import tasmota
from convert_vpype import convert_file
from config import config
# import RPi.GPIO as GPIO

globals.initialize()

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
PORT_RE = re.compile(r'^(/dev/[\w./-]+|COM\d+)$')
SPEED_RE = re.compile(r'^(\d+(\.\d+)?)?$')
HOST_RE = re.compile(r'^([A-Za-z0-9.-]+(:\d{1,5})?)?$')
CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f]')

# vpype commands that can execute code or touch arbitrary files
BLOCKED_VPYPE_COMMANDS = {'eval', 'script', 'read', 'write', 'forfile', 'include'}

SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')

# Held for the whole duration of a plot
plot_lock = threading.Lock()
current_plot = None

config_lock = threading.Lock()

for _section in ('telegram', 'tasmota', 'timelapse', 'plotter'):
    if not config.has_section(_section):
        config.add_section(_section)


# ////////////////////////////////////////////////////////////////////////////
# Access control

def auth_configured():
    return bool(config.get('auth', 'username', raw=True, fallback='')
                and config.get('auth', 'password', raw=True, fallback=''))


def is_authorized():
    """Optional HTTP basic auth, enabled by setting [auth] username/password in config.ini."""
    if not auth_configured():
        return True
    auth = request.authorization
    if auth is None:
        return False
    user_ok = hmac.compare_digest((auth.username or '').encode('utf-8'),
                                  config.get('auth', 'username', raw=True).encode('utf-8'))
    pass_ok = hmac.compare_digest((auth.password or '').encode('utf-8'),
                                  config.get('auth', 'password', raw=True).encode('utf-8'))
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
    if len(command) > 500:
        return 'Custom vpype command is too long'
    try:
        tokens = shlex.split(command)
    except ValueError:
        return 'Custom vpype command could not be parsed'
    for token in tokens:
        if token.lower() in BLOCKED_VPYPE_COMMANDS:
            return 'The vpype command "{}" is not allowed'.format(token)
        if '%' in token or '/' in token or '\\' in token:
            return 'Expressions and file paths are not allowed in custom vpype commands'
    return None


def plot(file, port, baudrate, flowControl, poweroff, timelapse):
    """Run a plot. Runs in a background task; the caller must already hold plot_lock."""
    global current_plot
    try:
        # Lock editing while printing
        socketio.emit('lock_edit', {'data': 'on'})

        # Tasmota - check for on
        # TODO this probable wont work for most plotters - still need to load page etc
        if poweroff == 'on':
            tasmota.tasmota_setStatus(socketio, 'on')
            time.sleep(2) # Just to be sure, wait 2 seconds

        # Start printing
        send2serial.sendToPlotter(socketio, str(file), str(port), int(baudrate), str(flowControl))

        # Tasmota - turn off plotter
        # TODO I think this may need a better solution.
        # There may still be data in the plotter buffer that needs to be plotteed
        if poweroff == 'on':
            print( "Sending power off command to Tasmota" )
            time.sleep(2) # Just to be sure, wait 2 seconds
            tasmota.tasmota_setStatus(socketio, 'off')
    except Exception as e:
        traceback.print_exc()
        socketio.emit('error', {'data': 'Plot failed: ' + repr(e)})
    finally:
        globals.printing = False
        globals.current_file = 'None'
        current_plot = None
        plot_lock.release()
        # Unlock editing
        socketio.emit('lock_edit', {'data': 'off'})


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
    return ports

#auto detect baud
@app.route('/update_baud', methods=['POST'])
def update_baud():
    port = request.form.get('selected_port', '')
    if not PORT_RE.match(port):
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
    if not PORT_RE.match(port):
        return 'Please select a valid COM port', 400
    baudrate = request.form.get('baudrate')
    if not valid_baudrate(baudrate):
        return 'Invalid baudrate', 400
    flowControl = request.form.get('flowControl')
    if flowControl not in FLOW_CONTROLS:
        return 'Invalid flow control', 400
    poweroff = request.form.get('tasmota')
    timelapse = request.form.get('timelapse')

    # Only one plot at a time
    if not plot_lock.acquire(blocking=False):
        return 'A plot is already running', 409
    current_plot = name

    # Plots run for a long time, so don't block the request
    socketio.start_background_task(plot, path, port, baudrate, flowControl, poweroff, timelapse)

    return 'Plot started'

# Stop the printing process
@app.route('/stop_plot', methods=['POST'])
def stop_plot():
    if not plot_lock.locked():
        # Make sure the UI is not left locked
        socketio.emit('lock_edit', {'data': 'off'})
        return 'No plot is running'

    globals.printing = False
    plotter_name = config.get('plotter', 'name', fallback='Plotter')
    socketio.start_background_task(
        notification.telegram_sendNotification,
        '{}: {}: Cancelled'.format(plotter_name, current_plot))
    globals.current_file = 'None'
    return 'Plot stopped'

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
    if not SPEED_RE.match(speed):
        return 'Invalid plot speed', 400
    error = check_vpype_command(custom_comand)
    if error:
        return error, 400

    try:
        output = convert_file(file, outputsize, pageorientation, device, speed, custom_comand, linemerge, linesort, linesimplify, reloop)
    except Exception as e:
        traceback.print_exc()
        socketio.emit('error', {'data': 'Conversion failed: ' + repr(e)})
        return 'File not converted.'

    return output

# Start reboot sequence
@app.route('/action_reboot', methods=['POST'])
def action_reboot():
    response = Response('action_reboot started')

    @response.call_on_close
    def on_close():
        subprocess.Popen(['sudo', '-n', 'reboot'])

    return response

# Start poweroff sequence
@app.route('/action_poweroff', methods=['POST'])
def action_poweroff():
    response = Response('action_poweroff started')

    @response.call_on_close
    def on_close():
        subprocess.Popen(['sudo', '-n', 'poweroff'])

    return response

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

def _is_text(value):
    return len(value) <= 200 and not CONTROL_CHARS_RE.search(value)

CONFIG_FIELDS = {
    'telegram_token': ('telegram', 'telegram_token', lambda v: re.fullmatch(r'[A-Za-z0-9:_-]*', v) is not None),
    'telegram_chatid': ('telegram', 'telegram_chatid', lambda v: re.fullmatch(r'[@\w-]*', v) is not None),
    'tasmota_enable': ('tasmota', 'tasmota_enable', _is_bool),
    'tasmota_ip': ('tasmota', 'tasmota_ip', lambda v: HOST_RE.match(v) is not None),
    'timelapse_enable': ('timelapse', 'timelapse_enable', _is_bool),
    'timelapse_auto_start': ('timelapse', 'timelapse_auto_start', _is_bool),
    'timelapse_preview': ('timelapse', 'timelapse_preview', _is_bool),
    'plotter_name': ('plotter', 'name', _is_text),
    'plotter_port': ('plotter', 'port', lambda v: v == '' or PORT_RE.match(v) is not None),
    'plotter_device': ('plotter', 'device', lambda v: v in DEVICES),
    'plotter_baudrate': ('plotter', 'baudrate', valid_baudrate),
    'plotter_flowControl': ('plotter', 'flowControl', lambda v: v in FLOW_CONTROLS),
}

# Update configfile values
@app.route('/save_configfile', methods=['GET', 'POST'])
def save_configfile():
    if request.method == "POST":
        updates = {}
        for field, (section, option, is_valid) in CONFIG_FIELDS.items():
            if field in request.form:
                value = request.form.get(field, '').strip()
                if not is_valid(value):
                    return 'Invalid value for {}'.format(field), 400
                updates[(section, option)] = value

        with config_lock:
            for (section, option), value in updates.items():
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

    output = {field: config.get(section, option, fallback='')
              for field, (section, option, _) in CONFIG_FIELDS.items()}
    return jsonify(output)

# On connection
@socketio.on('connect')
def on_connect(auth=None):
    # Socket.IO connections bypass before_request, so check access here as well
    if not is_authorized():
        return False
    # Sync the UI lock with the real state (e.g. after a page refresh during a plot)
    emit('lock_edit', {'data': 'on' if plot_lock.locked() else 'off'})

@socketio.event
def connection(message):
    print('Client connected')

if __name__ == "__main__":
    # use_reloader is off even in debug mode: it would restart the server mid-plot
    socketio.run(app, host='0.0.0.0', port=5000, debug=DEBUG, use_reloader=False, allow_unsafe_werkzeug=True)
