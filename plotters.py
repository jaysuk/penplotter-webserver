"""Plotter profiles: a named set of what a plotter needs (the vpype device, baud rate, flow control, pen
changes and every serial line option).

Two sources. The profiles shipped with a release are in `plotters_builtin.json` (the same format as an
export, so a profile somebody sends in can be pasted into it). The user's own are in
`userdata/plotters.json`, a folder the installer keeps across updates and a backup includes. A user's
profile with the same id as a built-in one replaces it; deleting it brings the built-in one back.

Profiles do not hold the port: the name of a serial port belongs to one machine, a profile is meant to be
shared. Files that come from outside (an import, a backup) are checked completely before anything is stored.
"""
import json
import os
import re
import threading

FORMAT = 1
APP = 'webplotter'
TYPE = 'plotters'

USER_DIR = 'userdata'
USER_FILE = os.path.join(USER_DIR, 'plotters.json')
BUILTIN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'plotters_builtin.json')

MAX_PROFILES = 100
MAX_FILE_BYTES = 256 * 1024

# What a profile can name. main.py and send2serial.py use the same lists.
DEVICES = {'hp7475a', 'hp7440a', 'hp7550', 'dxy', 'sketchmate', 'dmp_161',
           'designmate', 'artisan', 'mp4200'}
# HP-IB is not offered: it needs a separate shield for the Pi (send2serial still has its branches)
FLOW_CONTROLS = {'CTS/RTS', 'XON/XOFF', 'Software', 'None', 'CalComp'}
PEN_CHANGES = {'pause', 'auto'}

ID_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,39}')
# What vpype calls a device. The built-in ones are in DEVICES; the user can add their own (vpype_devices.py), so
# a profile only has to name one in the right way: whether it exists is checked when it is used
DEVICE_ID_RE = re.compile(r'[a-z][a-z0-9_]{0,29}')
CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f]')
NOTES_CONTROL_RE = re.compile(r'[\x00-\x09\x0b-\x1f\x7f]')     # a note may have line breaks
NUMBER_RE = re.compile(r'[0-9]{1,3}(\.[0-9]{1,3})?')
MAX_NAME = 60
MAX_NOTES = 300

# 'auto' leaves a line to what the flow control needs (or the operating system's default)
TRISTATE = ('auto', 'on', 'off')

# Serial line options: name -> (default, label, allowed values or None for a number)
LINE_FIELDS = {
    'bytesize': ('8', 'data bits', ('5', '6', '7', '8')),
    'parity': ('N', 'parity', ('N', 'E', 'O', 'M', 'S')),
    'stopbits': ('1', 'stop bits', ('1', '1.5', '2')),
    'xonxoff': ('auto', 'XON/XOFF', TRISTATE),
    'rtscts': ('auto', 'RTS/CTS', TRISTATE),
    'dsrdtr': ('auto', 'DSR/DTR', TRISTATE),
    'dtr': ('auto', 'DTR', TRISTATE),
    'rts': ('auto', 'RTS', TRISTATE),
    'timeout': ('', 'read timeout', None),
    'open_delay': ('0', 'delay after opening', None),
}
# A number field: (smallest, largest, empty allowed)
NUMBER_RANGES = {'timeout': (0.05, 60, True), 'open_delay': (0, 10, False)}

SETTING_FIELDS = ('device', 'baudrate', 'flowControl', 'pen_change') + tuple(LINE_FIELDS)


class PlotterError(ValueError):
    """A profile or a file of profiles is not usable. The message is for the user."""


def valid_baudrate(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9]+', value) is not None and 300 <= int(value) <= 921600


def _text(values, key):
    value = values.get(key)
    if value is None:
        return ''
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    return str(value).strip()


def clean_line(values):
    """The serial line options of a form or a profile, as canonical strings: (options, None), or
    (None, error message). A missing option gets its default."""
    clean = {}
    for key, (default, label, allowed) in LINE_FIELDS.items():
        value = _text(values, key)
        if value is None:
            return None, 'Invalid {}'.format(label)
        if value == '' and key != 'timeout':
            value = default
        if allowed is not None:
            if value not in allowed:
                return None, 'Invalid {}'.format(label)
        elif value != '':
            low, high, _ = NUMBER_RANGES[key]
            if not NUMBER_RE.fullmatch(value) or not low <= float(value) <= high:
                return None, 'Invalid {} ({} to {} s)'.format(label, '{:g}'.format(low), '{:g}'.format(high))
            value = '{:g}'.format(float(value))
        clean[key] = value
    return clean, None


def line_options(values):
    """What send2serial.open_port wants, from options that have been through clean_line (anything
    else gives the defaults). True/False/None for the on/off/auto lines."""
    clean, error = clean_line(values or {})
    if error:
        clean, _ = clean_line({})

    def tri(value):
        return None if value == 'auto' else value == 'on'

    stopbits = float(clean['stopbits'])
    return {
        'bytesize': int(clean['bytesize']), 'parity': clean['parity'],
        'stopbits': int(stopbits) if stopbits == int(stopbits) else stopbits,
        'xonxoff': tri(clean['xonxoff']), 'rtscts': tri(clean['rtscts']), 'dsrdtr': tri(clean['dsrdtr']),
        'dtr': tri(clean['dtr']), 'rts': tri(clean['rts']),
        'timeout': float(clean['timeout']) if clean['timeout'] else None,
        'open_delay': float(clean['open_delay']),
    }


def clean_settings(values):
    """The settings of a profile: (settings, None), or (None, error message)."""
    settings = {}
    device = _text(values, 'device') or 'hp7475a'
    baudrate = _text(values, 'baudrate') or '9600'
    flow_control = _text(values, 'flowControl') or 'CTS/RTS'
    pen_change = _text(values, 'pen_change') or 'auto'
    if not DEVICE_ID_RE.fullmatch(device):
        return None, 'Invalid plotter device'
    if not valid_baudrate(baudrate):
        return None, 'Invalid baudrate'
    if flow_control not in FLOW_CONTROLS:
        return None, 'Invalid flow control'
    if pen_change not in PEN_CHANGES:
        return None, 'Invalid pen change mode'
    settings.update(device=device, baudrate=baudrate, flowControl=flow_control, pen_change=pen_change)
    line, error = clean_line(values)
    if error:
        return None, error
    settings.update(line)
    return settings, None


def unknown_device(profiles, known):
    """An error message for the first profile whose device is not in `known`, or None. Profiles on disk are
    not checked this way (a device may have been deleted since); what comes in is."""
    for profile in profiles:
        device = profile['settings']['device']
        if device not in known:
            return ('{} uses the vpype device {}, which is not known here. Add it first (Plotter settings, '
                    'vpype devices).'.format(profile['name'], device))
    return None


def slug(name):
    """An id from a name: 'My Plotter (A3)' becomes 'my-plotter-a3'."""
    text = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:40].strip('-')
    return text or 'plotter'


def clean_profile(data):
    """One profile from a form, a file or a backup: (profile, None), or (None, error message).
    A profile without an id gets one from its name."""
    if not isinstance(data, dict):
        return None, 'A plotter is not written the way a plotter is'
    name = _text(data, 'name')
    if not name or len(name) > MAX_NAME or CONTROL_CHARS_RE.search(name) or '<' in name or '>' in name:
        return None, 'A plotter name is 1 to {} characters, without < or >'.format(MAX_NAME)
    notes = _text(data, 'notes')
    if notes is None or len(notes) > MAX_NOTES or NOTES_CONTROL_RE.search(notes):
        return None, 'The notes of {} are not text, or longer than {} characters'.format(name, MAX_NOTES)
    ident = _text(data, 'id') or slug(name)
    if not ID_RE.fullmatch(ident):
        return None, 'The id of {} is not valid'.format(name)
    # Settings sit in 'settings' in a file, and next to the name in a form
    if 'settings' in data and not isinstance(data['settings'], dict):
        return None, 'The settings of {} are not written the way settings are'.format(name)
    source = data.get('settings', data)
    settings, error = clean_settings(source)
    if error:
        return None, '{}: {}'.format(name, error)
    return {'id': ident, 'name': name, 'notes': notes, 'settings': settings}, None


def export_text(profiles):
    """The text of an export file."""
    return json.dumps({'app': APP, 'type': TYPE, 'format': FORMAT,
                       'plotters': [{'id': p['id'], 'name': p['name'], 'notes': p['notes'], 'settings': p['settings']}
                                    for p in profiles]}, indent=2) + '\n'


def parse_export(raw):
    """The profiles in an export file (bytes or text). Raises PlotterError; one bad profile
    refuses the whole file."""
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    if len(raw) > MAX_FILE_BYTES:
        raise PlotterError('The plotter file is too large')
    try:
        document = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        raise PlotterError('That is not a plotter file')
    if (not isinstance(document, dict) or document.get('app') != APP or document.get('type') != TYPE
            or not isinstance(document.get('plotters'), list)):
        raise PlotterError('That is not a web plotter plotter file')
    if document.get('format') != FORMAT:
        raise PlotterError('The plotter file was made by a newer version')
    if len(document['plotters']) > MAX_PROFILES:
        raise PlotterError('The plotter file has too many plotters')
    profiles, seen = [], set()
    for entry in document['plotters']:
        profile, error = clean_profile(entry)
        if error:
            raise PlotterError(error)
        if profile['id'] in seen:
            raise PlotterError('The plotter id {} is used twice'.format(profile['id']))
        seen.add(profile['id'])
        profiles.append(profile)
    return profiles


# ////////////////////////////////////////////////////////////////////////////
# The two stores

_lock = threading.Lock()


def builtin():
    """The profiles that come with this version."""
    try:
        with open(BUILTIN_FILE, 'rb') as f:
            return parse_export(f.read())
    except (OSError, PlotterError) as e:
        print('The built-in plotters cannot be read:', e)
        return []


def _load_custom():
    """(profiles, readable). A missing file is an empty list; a damaged one is reported and left alone."""
    try:
        with open(USER_FILE, 'rb') as f:
            return parse_export(f.read()), True
    except FileNotFoundError:
        return [], True
    except (OSError, PlotterError) as e:
        print('{} cannot be read: {}'.format(USER_FILE, e))
        return [], False


def custom():
    return _load_custom()[0]


def _write_custom(profiles):
    os.makedirs(USER_DIR, exist_ok=True)
    temp = USER_FILE + '.tmp'
    with open(temp, 'w', encoding='utf-8') as f:
        f.write(export_text(sorted(profiles, key=lambda p: p['name'].lower())))
    os.replace(temp, USER_FILE)


def all():
    """Every plotter, built-in ones first then the user's, as dicts with `source` ('builtin' or 'custom')
    and `replaces` (true for a user's profile that stands in for a built-in one)."""
    shipped = builtin()
    mine = custom()
    own = {p['id'] for p in mine}
    ids = {p['id'] for p in shipped}
    result = [dict(p, source='builtin', replaces=False) for p in shipped if p['id'] not in own]
    result += [dict(p, source='custom', replaces=p['id'] in ids) for p in mine]
    return result


def get(ident):
    for profile in all():
        if profile['id'] == ident:
            return profile
    return None


def save(profile):
    """Store a user's profile, replacing the one with its id. Returns None when saved, else the reason."""
    return save_many([profile])[1]


def save_many(profiles):
    """Store several. Returns (how many, None), or (0, reason): all or nothing."""
    with _lock:
        existing, readable = _load_custom()
        if not readable:
            return 0, '{} cannot be read: fix or remove it first'.format(USER_FILE)
        by_id = {p['id']: p for p in existing}
        for profile in profiles:
            by_id[profile['id']] = profile
        if len(by_id) > MAX_PROFILES:
            return 0, 'There are already {} plotters. Delete one first.'.format(MAX_PROFILES)
        try:
            _write_custom(list(by_id.values()))
        except OSError as e:
            print('Could not save the plotters:', repr(e))
            return 0, 'Could not save the plotters'
        return len(profiles), None


def replace_custom(profiles):
    """Make `profiles` the user's plotters (a restored backup). Raises OSError."""
    with _lock:
        _write_custom(profiles)


def delete(ident):
    """Remove a user's profile. Returns True if there was one."""
    with _lock:
        existing, readable = _load_custom()
        kept = [p for p in existing if p['id'] != ident]
        if not readable or len(kept) == len(existing):
            return False
        try:
            _write_custom(kept)
        except OSError as e:
            print('Could not delete the plotter:', repr(e))
            return False
        return True
