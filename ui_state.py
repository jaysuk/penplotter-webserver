"""What the page's forms were set to (plotter settings, conversion options, the text drawing form), kept on the
server so a reload, or another device, shows the same values.

The page sends `{group: {field: value}}`; a group is replaced as a whole, the others are kept. Nothing here
means anything to the server: the values go through the same validation as always when a plot or a conversion
is started. They are only text and booleans, and the page only puts them into fields that exist.

The file is `userdata/ui_state.json` (the installer's update keeps that folder). A damaged file is ignored.
"""
import json
import os
import re
import threading

USER_DIR = 'userdata'
STATE_FILE = os.path.join(USER_DIR, 'ui_state.json')

GROUPS = ('plotter', 'convert', 'text')
KEY_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,39}')
CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')
MAX_VALUE = 500
MAX_FIELDS = 80
MAX_BYTES = 64 * 1024

_lock = threading.Lock()


def clean(data):
    """The groups of a request: ({group: {field: str | bool}}, None), or (None, error message)."""
    if not isinstance(data, dict) or not data:
        return None, 'Nothing to save'
    result = {}
    for group, fields in data.items():
        if group not in GROUPS:
            return None, 'Unknown group {}'.format(str(group)[:40])
        if not isinstance(fields, dict) or len(fields) > MAX_FIELDS:
            return None, 'The group {} is not usable'.format(group)
        kept = {}
        for key, value in fields.items():
            if not isinstance(key, str) or not KEY_RE.fullmatch(key):
                return None, 'Invalid field name in {}'.format(group)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                value = str(value)
            if not isinstance(value, (str, bool)):
                return None, 'Invalid value for {}'.format(key)
            if isinstance(value, str) and (len(value) > MAX_VALUE or CONTROL_RE.search(value)):
                return None, 'Invalid value for {}'.format(key)
            kept[key] = value
        result[group] = kept
    return result, None


def load():
    """Everything that was saved: {group: {field: value}}. A missing or damaged file gives {}."""
    try:
        with open(STATE_FILE, 'rb') as f:
            raw = f.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return {}
        stored = json.loads(raw.decode('utf-8'))
    except (OSError, ValueError):
        return {}
    # Whatever is on disk goes through the same checks as a request
    result = {}
    if isinstance(stored, dict):
        for group in GROUPS:
            fields, error = clean({group: stored.get(group)}) if group in stored else (None, 'missing')
            if not error:
                result[group] = fields[group]
    return result


def save(data):
    """Merge the groups of a request into what is stored. Returns None when saved, else the reason."""
    groups, error = clean(data)
    if error:
        return error
    with _lock:
        merged = load()
        merged.update(groups)
        text = json.dumps(merged, indent=1, sort_keys=True)
        if len(text) > MAX_BYTES:
            return 'Too much to save'
        try:
            os.makedirs(USER_DIR, exist_ok=True)
            temp = STATE_FILE + '.tmp'
            with open(temp, 'w', encoding='utf-8') as f:
                f.write(text + '\n')
            os.replace(temp, STATE_FILE)
        except OSError as e:
            print('{} cannot be written: {}'.format(STATE_FILE, e.__class__.__name__))
            return 'Could not save'
    return None
