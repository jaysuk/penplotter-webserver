"""Plotter devices of your own for vpype.

vpype turns a drawing into HPGL for a *device*: the size of one plotter unit, the number of pens, and for each
paper size where the plotter's (0,0) is and how far it can reach. It ships with a list of them. A plotter that is
not on that list needs a device of its own, which vpype reads from a TOML file (`[device.<id>]` with one
`[[device.<id>.paper]]` per paper size).

The devices of the user are kept in `userdata/vpype_devices.toml` (the folder the installer keeps across updates and
a backup includes) in vpype's own format, so a device somebody else wrote can be pasted in. Everything that comes
from outside (a form, pasted text, a backup) is read with `parse`, which only accepts the keys and values vpype's
device files use and builds the text again from what it accepted. Nothing from the input is copied through.

`register()` puts the devices into vpype's configuration before a conversion.
"""
import json
import os
import re
import threading

try:
    import tomllib
except ImportError:             # Python < 3.11: vpype needs tomli, so it is installed
    import tomli as tomllib

import plotters

USER_DIR = 'userdata'
USER_FILE = os.path.join(USER_DIR, 'vpype_devices.toml')

MAX_BYTES = 128 * 1024
MAX_DEVICES = 30
MAX_PAPERS = 20
ID_RE = plotters.DEVICE_ID_RE
PAPER_RE = re.compile(r'[A-Za-z0-9_]{1,20}')
UNIT_RE = re.compile(r'[0-9]{1,3}(\.[0-9]{1,6})?mm')
LENGTH_RE = re.compile(r'(-?[0-9]{1,5}(\.[0-9]{1,3})?)(mm|in)')
PU_RE = re.compile(r'[0-9, -]{0,40}')
TEXT_RE = re.compile(r'[^\x00-\x08\x0b-\x1f\x7f<>]*')       # a line break is fine, markup is not
MAX_TEXT = 300
UNIT_RANGE_MM = (0.001, 10.0)
LIMIT = 10 ** 7                 # largest plotter coordinate (a plotter unit is a fraction of a millimetre)

# The sizes the page offers (vpype knows each by name) and the size of the paper in mm, wide side first
SIZES = {'a4': (297, 210), 'a3': (420, 297), 'a2': (594, 420), 'a1': (841, 594), 'a0': (1189, 841)}


class DeviceError(ValueError):
    """A device is not usable. The message is for the user."""


# ////////////////////////////////////////////////////////////////////////////
# Checking

def _text(table, key, label, default=''):
    value = table.get(key, default)
    if not isinstance(value, str) or len(value) > MAX_TEXT or not TEXT_RE.fullmatch(value):
        raise DeviceError('{} is not text of up to {} characters without < or >'.format(label, MAX_TEXT))
    return value.strip()


def _number(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise DeviceError('{} must be a whole number from {} to {}'.format(label, low, high))
    return value


def _pair(table, key, label, check):
    value = table.get(key)
    if not isinstance(value, list) or len(value) != 2:
        raise DeviceError('{} must be two values, like ["10mm", "5mm"]'.format(label))
    return [check(item, label) for item in value]


def _length(item, label):
    match = LENGTH_RE.fullmatch(item) if isinstance(item, str) else None
    if not match:
        raise DeviceError('{} must be lengths like "297mm" or "11in"'.format(label))
    return item


def _positive_length(item, label):
    if float(LENGTH_RE.fullmatch(_length(item, label)).group(1)) <= 0:
        raise DeviceError('{} must be more than 0'.format(label))
    return item


def _range(table, key, label):
    value = table.get(key)
    if (not isinstance(value, list) or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) or abs(v) > LIMIT for v in value)
            or value[0] >= value[1]):
        raise DeviceError('{} must be two whole numbers, the smaller first, like [0, 11040]'.format(label))
    return value


def _flag(table, key, label, default):
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise DeviceError('{} must be true or false'.format(label))
    return value


def _unknown(table, allowed, label):
    extra = sorted(set(table) - set(allowed))
    if extra:
        raise DeviceError('{} has settings vpype does not know: {}'.format(label, ', '.join(str(key)[:30] for key in extra)))


PAPER_KEYS = ('name', 'aka_names', 'info', 'paper_size', 'paper_orientation', 'x_range', 'y_range', 'y_axis_up',
              'origin_location', 'origin_location_reference', 'rotate_180', 'final_pu_params', 'set_ps')
DEVICE_KEYS = ('name', 'plotter_unit_length', 'pen_count', 'info', 'paper')


def _paper(table, where):
    if not isinstance(table, dict):
        raise DeviceError('{}: a paper is a table of settings'.format(where))
    _unknown(table, PAPER_KEYS, where)
    name = table.get('name')
    if not isinstance(name, str) or not PAPER_RE.fullmatch(name):
        raise DeviceError('{}: the paper needs a name of letters, numbers and _ (up to 20)'.format(where))
    where = '{} paper {}'.format(where, name)
    paper = {'name': name}
    aka = table.get('aka_names', [])
    if not isinstance(aka, list) or len(aka) > 10 or not all(isinstance(a, str) and PAPER_RE.fullmatch(a) for a in aka):
        raise DeviceError('{}: aka_names must be a list of names'.format(where))
    if aka:
        paper['aka_names'] = aka
    info = _text(table, 'info', where + ' info')
    if info:
        paper['info'] = info
    if 'paper_size' in table:
        paper['paper_size'] = _pair(table, 'paper_size', where + ' paper_size', _positive_length)
    if 'paper_orientation' in table:
        if table['paper_orientation'] not in ('portrait', 'landscape'):
            raise DeviceError('{}: paper_orientation is "portrait" or "landscape"'.format(where))
        paper['paper_orientation'] = table['paper_orientation']
    if 'paper_size' not in paper and 'paper_orientation' not in paper:
        raise DeviceError('{}: give paper_size, or paper_orientation for a paper of any size'.format(where))
    for key in ('x_range', 'y_range'):
        if key in table:
            paper[key] = _range(table, key, '{} {}'.format(where, key))
    for key in ('y_axis_up', 'origin_location'):
        if key not in table:
            raise DeviceError('{}: {} is missing'.format(where, key))
    paper['y_axis_up'] = _flag(table, 'y_axis_up', where + ' y_axis_up', False)
    paper['origin_location'] = _pair(table, 'origin_location', where + ' origin_location', _length)
    reference = table.get('origin_location_reference', 'topleft')
    if reference not in ('topleft', 'botleft'):
        raise DeviceError('{}: origin_location_reference is "topleft" or "botleft"'.format(where))
    if 'origin_location_reference' in table:
        paper['origin_location_reference'] = reference
    if _flag(table, 'rotate_180', where + ' rotate_180', False):
        paper['rotate_180'] = True
    if 'final_pu_params' in table:
        value = table['final_pu_params']
        if not isinstance(value, str) or not PU_RE.fullmatch(value):
            raise DeviceError('{}: final_pu_params is numbers, like "0,0"'.format(where))
        paper['final_pu_params'] = value
    if 'set_ps' in table:
        paper['set_ps'] = _number(table['set_ps'], 0, 20, where + ' set_ps')
    return paper


def _device(ident, table):
    where = 'Device {}'.format(ident)
    if not ID_RE.fullmatch(ident):
        raise DeviceError('A device id is lower case letters, numbers and _, starting with a letter (up to 30): {}'.format(ident[:40]))
    if not isinstance(table, dict):
        raise DeviceError('{} is not a table of settings'.format(where))
    _unknown(table, DEVICE_KEYS, where)
    device = {'name': _text(table, 'name', where + ' name', ident) or ident}
    unit = table.get('plotter_unit_length')
    if not isinstance(unit, str) or not UNIT_RE.fullmatch(unit):
        raise DeviceError('{} plotter_unit_length must be a size in mm, like "0.025mm"'.format(where))
    if not UNIT_RANGE_MM[0] <= float(unit[:-2]) <= UNIT_RANGE_MM[1]:
        raise DeviceError('{} plotter_unit_length must be from {} to {} mm'.format(where, *UNIT_RANGE_MM))
    device['plotter_unit_length'] = unit
    device['pen_count'] = _number(table.get('pen_count'), 1, 99, where + ' pen_count')
    info = _text(table, 'info', where + ' info')
    if info:
        device['info'] = info
    papers = table.get('paper')
    if not isinstance(papers, list) or not 1 <= len(papers) <= MAX_PAPERS:
        raise DeviceError('{} needs 1 to {} [[device.{}.paper]] sections'.format(where, MAX_PAPERS, ident))
    device['paper'] = [_paper(paper, where) for paper in papers]
    names = [name for paper in device['paper'] for name in [paper['name']] + paper.get('aka_names', [])]
    if len(set(names)) != len(names):
        raise DeviceError('{} uses a paper name twice'.format(where))
    return device


def parse(text, reserved=()):
    """The devices in some text of vpype's device format, as {id: device} (cleaned). Raises DeviceError.
    `reserved` are ids that cannot be used (the devices vpype comes with)."""
    if isinstance(text, bytes):
        try:
            text = text.decode('utf-8')
        except UnicodeDecodeError:
            raise DeviceError('That is not text')
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise DeviceError('The device text is too large')
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise DeviceError('That is not valid TOML: {}'.format(str(e)[:200]))
    if set(document) - {'device'}:
        raise DeviceError('Only [device.<id>] sections are used here (found: {})'.format(
            ', '.join(sorted(str(key)[:30] for key in set(document) - {'device'}))))
    tables = document.get('device', {})
    if not isinstance(tables, dict):
        raise DeviceError('[device] must hold the devices, as [device.<id>]')
    if len(tables) > MAX_DEVICES:
        raise DeviceError('Too many devices (up to {})'.format(MAX_DEVICES))
    devices = {}
    for ident, table in tables.items():
        if ident in reserved:
            raise DeviceError('{} is the name of a device that comes with vpype: choose another id'.format(ident))
        devices[ident] = _device(ident, table)
    return devices


# ////////////////////////////////////////////////////////////////////////////
# Writing

def _value(value):
    """TOML for a checked value (a string, a whole number, a boolean, or a list of them)."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)        # a JSON string is a TOML basic string
    return '[' + ', '.join(_value(item) for item in value) + ']'


def to_toml(devices):
    """Text for {id: device} (as `parse` returns it), the way vpype's own file is laid out."""
    blocks = []
    for ident in sorted(devices):
        device = devices[ident]
        lines = ['[device.{}]'.format(ident)]
        lines += ['{} = {}'.format(key, _value(device[key])) for key in ('name', 'plotter_unit_length', 'pen_count', 'info')
                  if key in device]
        blocks.append('\n'.join(lines))
        for paper in device['paper']:
            lines = ['[[device.{}.paper]]'.format(ident)]
            lines += ['{} = {}'.format(key, _value(paper[key])) for key in PAPER_KEYS if key in paper]
            blocks.append('\n'.join(lines))
    return '\n\n'.join(blocks) + '\n' if blocks else ''


# ////////////////////////////////////////////////////////////////////////////
# A device from a few values

def _mm(value):
    return '{:g}mm'.format(round(value, 3))


def _number_field(values, key, label, low, high):
    text = str(values.get(key) if values.get(key) not in (None, '') else '').strip()
    if not re.fullmatch(r'-?[0-9]{1,5}(\.[0-9]{1,6})?', text):
        raise DeviceError('{} must be a number'.format(label))
    value = float(text)
    if not low <= value <= high:
        raise DeviceError('{} must be from {:g} to {:g}'.format(label, low, high))
    return value


def generate(values):
    """The text of a device from the quick form: the plotter's name, the size of a plotter unit, its pens, the
    corner of the paper where (0,0) is, and the paper sizes. Raises DeviceError."""
    name = str(values.get('name') or '').strip()
    if not name or len(name) > 60 or not TEXT_RE.fullmatch(name) or '\n' in name:
        raise DeviceError('Give the device a name (up to 60 characters, without < or >)')
    ident = plotters.slug(name).replace('-', '_')
    if not ID_RE.fullmatch(ident):
        ident = 'device_' + ident
    ident = ident[:30].rstrip('_')
    unit = _number_field(values, 'unit', 'The plotter unit', *UNIT_RANGE_MM)
    pens = _number_field(values, 'pens', 'The number of pens', 1, 99)
    if pens != int(pens):
        raise DeviceError('The number of pens must be a whole number')
    offset_x = _number_field(values, 'origin_x', 'The origin across', 0, 100)
    offset_y = _number_field(values, 'origin_y', 'The origin along', 0, 100)
    corner = values.get('origin')
    if corner not in ('botleft', 'topleft'):
        raise DeviceError('Choose the corner of the paper where the plotter starts counting')
    sizes = values.get('sizes') or []
    if isinstance(sizes, str):
        sizes = [sizes]
    sizes = [size for size in SIZES if size in sizes]
    if not sizes:
        raise DeviceError('Choose at least one paper size')

    papers = []
    for size in sizes:
        width, height = SIZES[size]
        paper = {'name': size, 'paper_size': [_mm(width), _mm(height)]}
        # The plotter counts from the corner (plus the offset) towards the far edges, and nothing is drawn
        # beyond the paper
        paper['x_range'] = [0, int(round((width - offset_x) / unit))]
        paper['y_range'] = [0, int(round((height - offset_y) / unit))]
        paper['y_axis_up'] = corner == 'botleft'
        paper['origin_location'] = [_mm(offset_x), _mm(offset_y)]
        if corner == 'botleft':
            paper['origin_location_reference'] = 'botleft'
        if values.get('rotate_180'):
            paper['rotate_180'] = True
        papers.append(paper)
    device = {'name': name, 'plotter_unit_length': '{:g}mm'.format(unit), 'pen_count': int(pens), 'paper': papers}
    return to_toml(parse(to_toml({ident: device}), reserved=()))


# ////////////////////////////////////////////////////////////////////////////
# vpype's own devices

def _vpype_config():
    """vpype's device table, or None when vpype is not installed (a test, or a machine without it)."""
    try:
        import vpype
        return vpype.config_manager.config.setdefault('device', {})
    except ImportError:
        return None


_registered = set()
_lock = threading.Lock()


def builtin_ids():
    """The devices vpype comes with. Without vpype, the ones the page lists, except mp4200, which vpype 1.15 lacks."""
    with _lock:
        table = _vpype_config()
        if table is None:
            return set(plotters.DEVICES) - {'mp4200'}
        return set(table) - _registered


def builtin_text(ident):
    """A device that comes with vpype as text, to start a device of your own from. None if there is none."""
    with _lock:
        table = _vpype_config()
        source = table.get(ident) if table is not None and ident not in _registered else None
        if not isinstance(source, dict):
            return None
        copy = {key: source[key] for key in DEVICE_KEYS if key in source and key != 'paper'}
        copy['paper'] = [{key: paper[key] for key in PAPER_KEYS if key in paper} for paper in source.get('paper', [])]
        copy['name'] = str(copy.get('name', ident)) + ' (copy)'
        return to_toml({ident + '_copy': copy})


# ////////////////////////////////////////////////////////////////////////////
# The user's devices

def _load():
    """(devices, readable). A missing file is no devices; a damaged one is reported and left alone."""
    try:
        with open(USER_FILE, 'rb') as f:
            return parse(f.read(MAX_BYTES + 1)), True
    except FileNotFoundError:
        return {}, True
    except (OSError, DeviceError) as e:
        print('{} cannot be read: {}'.format(USER_FILE, e))
        return {}, False


def custom():
    """The user's devices: {id: device}."""
    return _load()[0]


def ids():
    return set(custom())


def _write(devices):
    os.makedirs(USER_DIR, exist_ok=True)
    temp = USER_FILE + '.tmp'
    with open(temp, 'w', encoding='utf-8') as f:
        f.write(to_toml(devices))
    os.replace(temp, USER_FILE)


def describe():
    """What the page shows: each device with its text (to edit) and the paper sizes it has."""
    return [{'id': ident, 'name': device['name'], 'info': device.get('info', ''), 'unit': device['plotter_unit_length'],
             'pens': device['pen_count'], 'papers': [paper['name'] for paper in device['paper']],
             'text': to_toml({ident: device})} for ident, device in sorted(custom().items())]


def save(text, replace=None):
    """Store the devices in `text`, each replacing the one with its id. `replace` is the id of a device being
    edited: when the text has another id, the old one goes (a rename). Returns the saved ids; raises DeviceError."""
    devices = parse(text, reserved=builtin_ids())
    if not devices:
        raise DeviceError('There is no [device.<id>] in that text')
    with _lock:
        existing, readable = _load()
        if not readable:
            raise DeviceError('{} cannot be read: fix or remove it first'.format(USER_FILE))
        if replace and replace not in devices:
            existing.pop(replace, None)
        existing.update(devices)
        if len(existing) > MAX_DEVICES:
            raise DeviceError('There are already {} devices. Delete one first.'.format(MAX_DEVICES))
        try:
            _write(existing)
        except OSError as e:
            print('Could not save the devices:', repr(e))
            raise DeviceError('Could not save the devices')
    return sorted(devices)


def delete(ident):
    """Remove one of the user's devices. Returns True if there was one."""
    with _lock:
        existing, readable = _load()
        if not readable or ident not in existing:
            return False
        del existing[ident]
        try:
            _write(existing)
        except OSError as e:
            print('Could not delete the device:', repr(e))
            return False
        return True


def replace_all(devices):
    """Make `devices` ({id: device}, from `parse`) the user's devices (a restored backup). Raises OSError."""
    with _lock:
        _write(devices)


def papers(ident):
    """The paper names a device of the user has, or None when it is not one of theirs."""
    device = custom().get(ident)
    if device is None:
        return None
    return {name for paper in device['paper'] for name in [paper['name']] + paper.get('aka_names', [])}


def register():
    """Put the user's devices into vpype's configuration (and take out ones that were deleted or renamed). Call it
    before converting. Returns the ids registered; without vpype there is nothing to do."""
    with _lock:
        table = _vpype_config()
        if table is None:
            return []
        devices, _ = _load()
        for ident in _registered:
            table.pop(ident, None)
        _registered.clear()
        for ident, device in devices.items():
            if ident in table:
                continue            # a device that comes with vpype keeps its name
            table[ident] = json.loads(json.dumps(device))
            _registered.add(ident)
        return sorted(_registered)
