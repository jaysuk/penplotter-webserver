"""Read HPGL files without plotting them: size of the drawing, pens used, pen-up and pen-down
distance, and a rough time estimate.

The parser streams the file, so a big plot does not have to fit in memory. Everything here is
plain Python (no serial port, no Flask) so it can be tested on its own.

Offsets are byte offsets into the file. The sender uses them to stop at a pen change, and the
time marks turn the number of bytes sent into "time left".
"""
import hashlib
import json
import math
import os
import re
import shutil

# vpype's HP plotter profiles use 0.02488 mm per plotter unit (about 40.2 units per mm)
UNITS_PER_MM = 1 / 0.02488

# Files larger than this are not analysed (the parse is slow on a Pi Zero)
MAX_ANALYSE_BYTES = 50 * 1024 * 1024

# A rough model of how long a plotter takes. The numbers are guesses for an HP 7475A class
# plotter; the app corrects them with the ratio of estimated to actual time of finished plots.
DEFAULT_MODEL = {
    'speed_cm_s': 38.1,        # pen down, when the file has no VS command
    'travel_cm_s': 38.1,       # pen up
    'vector_s': 0.003,         # per drawn vector (acceleration)
    'travel_move_s': 0.03,     # per pen-up move
    'pen_lift_s': 0.1,         # each time the pen goes up or down
}

MARK_EVERY = 2048              # bytes between entries of the time table
CHUNK = 1 << 20
CACHE_DIR = os.path.join('cache', 'analysis')
VERSION = 2

# One command: an LB label (ends with ETX), or anything up to the next ; or newline
_TOKEN = re.compile(rb'[ \t\r\n]*(?:(LB[^\x03]*)\x03|(?!LB)([^;\n]*)[;\n])', re.IGNORECASE)


def iter_commands(f, chunk=CHUNK):
    """Yield (start, end, code, args) for each command of an open binary file.

    `start` is the offset of the command's first character, `end` the offset just after its
    terminator, `code` its two letters in upper case and `args` the rest as bytes. Reading starts
    at the file's current position (which is offset 0 of the first command)."""
    base = f.tell()
    buf = b''
    while True:
        data = f.read(chunk)
        buf += data
        pos = 0
        while True:
            m = _TOKEN.match(buf, pos)
            if m is None:
                break
            if m.group(1) is not None:
                text, start = m.group(1), m.start(1)
            else:
                text, start = m.group(2), m.start(2)
            pos = m.end()
            text = text.rstrip()
            if text:
                yield base + start, base + pos, text[:2].upper().decode('latin-1'), text[2:]
        buf = buf[pos:]
        base += pos
        if not data:
            break
    tail = buf.strip()
    if tail:
        start = base + len(buf) - len(buf.lstrip())
        yield start, base + len(buf), tail[:2].upper().decode('latin-1'), tail[2:]


def parse_numbers(args):
    """The numbers of a command's argument list, or [] if it does not parse."""
    args = args.strip()
    if not args:
        return []
    try:
        return [float(part) for part in args.split(b',')]
    except ValueError:
        return []


DEFAULT_CHORD = 5.0     # degrees per straight piece of a circle or arc (the HP-GL default)


def arc_points(cx, cy, x0, y0, sweep, chord=DEFAULT_CHORD):
    """Points along an arc round (cx, cy) from (x0, y0), `sweep` degrees (counter-clockwise when
    positive), without the start point. Empty for an arc with no radius or no sweep."""
    radius = math.hypot(x0 - cx, y0 - cy)
    if radius == 0 or sweep == 0:
        return []
    chord = min(max(abs(chord), 0.5), 90.0)
    steps = max(1, math.ceil(abs(sweep) / chord))
    start = math.atan2(y0 - cy, x0 - cx)
    return [(cx + radius * math.cos(start + math.radians(sweep) * i / steps),
             cy + radius * math.sin(start + math.radians(sweep) * i / steps)) for i in range(1, steps + 1)]


class State:
    """The plotter's drawing state, advanced one command at a time."""

    def __init__(self):
        self.x = 0.0
        self.y = 0.0
        self.pen = 0
        self.pen_down = False
        self.absolute = True
        self.speed = None      # cm/s from VS, None for the plotter's default

    def apply(self, code, args):
        """Apply a command. Returns a list of moves [(x0, y0, x1, y1, pen_down)] it caused."""
        if code in ('IN', 'DF'):
            self.absolute = True
            self.pen_down = False
            if code == 'IN':
                self.speed = None
            return []
        if code == 'SP':
            numbers = parse_numbers(args)
            self.pen = int(numbers[0]) if numbers else 0
            return []
        if code == 'VS':
            numbers = parse_numbers(args)
            self.speed = numbers[0] if numbers and numbers[0] > 0 else None
            return []
        if code in ('CI', 'AA', 'AR'):
            return self._curve(code, parse_numbers(args))
        if code in ('PA', 'PR', 'PU', 'PD'):
            if code == 'PA':
                self.absolute = True
            elif code == 'PR':
                self.absolute = False
            elif code == 'PU':
                self.pen_down = False
            else:
                self.pen_down = True
            numbers = parse_numbers(args)
            moves = []
            for i in range(0, len(numbers) - 1, 2):
                x = numbers[i] if self.absolute else self.x + numbers[i]
                y = numbers[i + 1] if self.absolute else self.y + numbers[i + 1]
                moves.append((self.x, self.y, x, y, self.pen_down))
                self.x, self.y = x, y
            return moves
        return []

    def _curve(self, code, numbers):
        """A circle (CI) or an arc (AA absolute, AR relative centre). The plotter lowers the pen
        for it whatever the pen state is. Returns the moves, as `apply` does."""
        moves = []
        if code == 'CI':
            if not numbers or numbers[0] <= 0:
                return moves
            radius = numbers[0]
            cx, cy = self.x, self.y
            moves.append((cx, cy, cx + radius, cy, False))      # out to the circle
            x0, y0 = cx + radius, cy
            for x, y in arc_points(cx, cy, x0, y0, 360, numbers[1] if len(numbers) > 1 else DEFAULT_CHORD):
                moves.append((x0, y0, x, y, True))
                x0, y0 = x, y
            moves.append((x0, y0, cx, cy, False))               # and back to the centre
            return moves
        if len(numbers) < 3:
            return moves
        cx, cy = (numbers[0], numbers[1]) if code == 'AA' else (self.x + numbers[0], self.y + numbers[1])
        x0, y0 = self.x, self.y
        for x, y in arc_points(cx, cy, x0, y0, numbers[2], numbers[3] if len(numbers) > 3 else DEFAULT_CHORD):
            moves.append((x0, y0, x, y, True))
            x0, y0 = x, y
        self.x, self.y = x0, y0
        return moves


def _new_segment(pen, start):
    return {'pen': pen, 'start': start, 'end': None, 'draw_length': 0.0, 'travel_length': 0.0,
            'paths': 0, 'seconds': 0.0}


def analyze(path, units_per_mm=UNITS_PER_MM, model=None):
    """Analyse an HPGL file. Returns a JSON friendly dict:

    bytes, bounds ([minx, miny, maxx, maxy] of the drawn lines, or None), draw_length and
    travel_length (plotter units), paths, seconds (estimate), relative (the file uses PR),
    unsupported (drawing commands that are not counted), segments and marks.

    `segments` lists, in plotting order, each stretch of the file drawn with one pen:
    {pen, start, end, draw_length, travel_length, paths, seconds}. A segment starts at its SP
    command, so a pen change is always at a segment's `start`. `marks` is [[offset, seconds]]
    every few KB, for turning the bytes sent into the estimated time spent."""
    model = dict(DEFAULT_MODEL, **(model or {}))
    unit_cm = 1.0 / (units_per_mm * 10.0)       # centimetres per plotter unit
    size = os.path.getsize(path)

    state = State()
    bounds = [math.inf, math.inf, -math.inf, -math.inf]
    draw_length = travel_length = 0.0
    paths = 0
    seconds = 0.0
    relative = False
    unsupported = set()
    segments = []
    segment = None
    marks = [[0, 0.0]]
    next_mark = MARK_EVERY

    def close_segment(offset):
        nonlocal segment
        if segment is not None:
            segment['end'] = offset
            segments.append(segment)
            segment = None

    with open(path, 'rb') as f:
        for start, end, code, args in iter_commands(f):
            if code == 'PR':
                relative = True
            elif code in ('EA', 'ER', 'EW', 'EP', 'FP', 'RA', 'RR', 'WG', 'LB'):
                unsupported.add(code)

            was_down = state.pen_down
            if code == 'SP':
                close_segment(start)
            moves = state.apply(code, args)
            if code == 'SP' and state.pen > 0:
                segment = _new_segment(state.pen, start)

            spent = 0.0
            if code in ('PU', 'PD') and state.pen_down != was_down:
                spent += model['pen_lift_s']
            for x0, y0, x1, y1, down in moves:
                length = math.hypot(x1 - x0, y1 - y0)
                if down:
                    if not was_down:
                        paths += 1
                        if segment is not None:
                            segment['paths'] += 1
                    speed = state.speed or model['speed_cm_s']
                    spent += length * unit_cm / speed + model['vector_s']
                    draw_length += length
                    if segment is not None:
                        segment['draw_length'] += length
                    for px, py in ((x0, y0), (x1, y1)):
                        bounds[0] = min(bounds[0], px)
                        bounds[1] = min(bounds[1], py)
                        bounds[2] = max(bounds[2], px)
                        bounds[3] = max(bounds[3], py)
                else:
                    spent += length * unit_cm / model['travel_cm_s'] + model['travel_move_s']
                    travel_length += length
                    if segment is not None:
                        segment['travel_length'] += length
                was_down = down
            seconds += spent
            if segment is not None:
                segment['seconds'] += spent

            if end >= next_mark:
                marks.append([end, seconds])
                next_mark = end + MARK_EVERY

    close_segment(size)
    marks.append([size, seconds])
    return {
        'version': VERSION,
        'bytes': size,
        'bounds': bounds if bounds[0] != math.inf else None,
        'draw_length': draw_length,
        'travel_length': travel_length,
        'paths': paths,
        'seconds': seconds,
        'relative': relative,
        'unsupported': sorted(unsupported),
        'units_per_mm': units_per_mm,
        'segments': segments,
        'marks': marks,
    }


def time_at(analysis, offset):
    """Estimated seconds of plotting that the first `offset` bytes of the file take."""
    marks = analysis['marks']
    if offset <= marks[0][0]:
        return marks[0][1]
    if offset >= marks[-1][0]:
        return marks[-1][1]
    lo, hi = 0, len(marks) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if marks[mid][0] <= offset:
            lo = mid
        else:
            hi = mid
    (o0, t0), (o1, t1) = marks[lo], marks[hi]
    return t0 + (t1 - t0) * (offset - o0) / (o1 - o0)


def pens_used(analysis):
    """Per pen totals, in order of first use: [{pen, draw_length, travel_length, paths, seconds}]."""
    totals = {}
    for segment in analysis['segments']:
        entry = totals.setdefault(segment['pen'], {'pen': segment['pen'], 'draw_length': 0.0,
                                                   'travel_length': 0.0, 'paths': 0, 'seconds': 0.0})
        for key in ('draw_length', 'travel_length', 'paths', 'seconds'):
            entry[key] += segment[key]
    return list(totals.values())


def pens_drawn(analysis, offset=None):
    """Millimetres of pen-down travel per pen for the first `offset` bytes of the analysed file
    (all of it by default): {pen: mm}. A pen segment that was only partly sent counts for the
    share of its bytes that were."""
    if analysis is None:
        return {}
    offset = analysis['bytes'] if offset is None else offset
    drawn = {}
    for segment in analysis['segments']:
        span = segment['end'] - segment['start']
        if span <= 0 or offset <= segment['start']:
            continue
        share = min(1.0, (offset - segment['start']) / span)
        drawn[segment['pen']] = drawn.get(segment['pen'], 0.0) + segment['draw_length'] * share / analysis['units_per_mm']
    return drawn


def pen_changes(analysis):
    """Offsets where the plot moves on to a different pen, as [(offset, pen)].

    The first pen is not a change (it has to be loaded before the plot starts), and neither is
    selecting the pen that is already in use."""
    changes = []
    previous = None
    for segment in analysis['segments']:
        if previous is not None and segment['pen'] != previous:
            changes.append((segment['start'], segment['pen']))
        previous = segment['pen']
    return changes


def _render(code, args):
    """A command as bytes, terminated the way plotters expect."""
    return code.encode('latin-1') + args.strip() + (b'' if code == 'LB' else b';')


def filter_pens(src, dst, pens, analysis):
    """Write a copy of `src` that only draws with the given pens. Returns the new file's size.

    Everything outside the pen segments (setup at the start, the end of the file) is kept. After
    a skipped segment the pen is no longer where the original file expects it, and files from
    vpype use relative coordinates (PR), so the next command is preceded by an absolute move to
    the position the original would have had."""
    wanted = {int(p) for p in pens}
    segments = analysis['segments']
    if not wanted & {segment['pen'] for segment in segments}:
        raise ValueError('The file does not draw with any of the selected pens')

    state = State()
    index = 0
    need_sync = False
    written = 0
    with open(src, 'rb') as f, open(dst, 'wb') as out:
        for start, end, code, args in iter_commands(f):
            while index < len(segments) and start >= segments[index]['end']:
                index += 1
            segment = segments[index] if index < len(segments) else None
            inside = segment is not None and segment['start'] <= start

            x, y, absolute = state.x, state.y, state.absolute
            state.apply(code, args)
            if inside and segment['pen'] not in wanted:
                need_sync = True
                continue

            if need_sync:
                prefix = b'PU;PA%d,%d;' % (round(x), round(y)) + (b'' if absolute else b'PR;')
                out.write(prefix)
                written += len(prefix)
                need_sync = False
            data = _render(code, args)
            out.write(data)
            written += len(data)
    return written


# Plot-time changes (see tweak_file)
SPEED_RANGE = (0.1, 100.0)         # cm/s, VS
FORCE_RANGE = (1, 128)             # grams, FS
ACCEL_RANGE = (1, 32)              # AS
MAX_OFFSET_MM = 500
# Commands that put the drawing in a different place when the origin is moved: a file that scales
# or windows its own coordinate system cannot be shifted in plotter units
NO_OFFSET_COMMANDS = {'SC', 'IW', 'IP', 'RO'}
OVERRIDDEN = {'VS', 'FS', 'AS'}


def _shift(numbers, dx, dy):
    """Pairs of numbers moved by (dx, dy), as an argument list."""
    return b','.join(b'%d' % round(value + (dx if i % 2 == 0 else dy)) for i, value in enumerate(numbers))


def tweak_file(src, dst, speed=None, force=None, accel=None, dx=0, dy=0):
    """Write a copy of `src` for one plot: with the pen speed (VS, cm/s), pen force (FS) and
    acceleration (AS) the plotter should use, and the drawing moved by (dx, dy) plotter units.

    The speed, force and acceleration replace the ones in the file: each is sent after every IN or DF
    (which reset them) and the file's own commands for them are left out. An offset moves the
    absolute coordinates; coordinates after PR are relative to those, so they follow. A file that sets its own
    scaling or window (SC, IW, IP, RO) cannot be moved. Returns the new file's size."""
    overrides = b''
    if speed is not None:
        overrides += b'VS%g;' % speed
    if force is not None:
        overrides += b'FS%d;' % force
    if accel is not None:
        overrides += b'AS%d;' % accel
    dropped = {code for code, value in (('VS', speed), ('FS', force), ('AS', accel)) if value is not None}
    moved = bool(dx or dy)

    state = State()
    written = 0
    first = True
    with open(src, 'rb') as f, open(dst, 'wb') as out:
        def put(data):
            nonlocal written
            out.write(data)
            written += len(data)

        for start, end, code, args in iter_commands(f):
            if first and code not in ('IN', 'DF') and overrides:
                put(overrides)      # a file that does not start with IN;
            first = False
            if moved and code in NO_OFFSET_COMMANDS:
                raise ValueError('This file sets its own scaling or window ({}): it cannot be moved'.format(code))
            if code in dropped:
                state.apply(code, args)
                continue
            data = _render(code, args)
            if moved:
                numbers = parse_numbers(args)
                absolute = code == 'PA' or (code in ('PU', 'PD') and state.absolute)
                if numbers and (absolute or code in ('EA', 'RA')) and len(numbers) % 2 == 0:
                    data = code.encode('latin-1') + _shift(numbers, dx, dy) + b';'
                elif code == 'AA' and len(numbers) >= 3:
                    data = b'AA' + _shift(numbers[:2], dx, dy) + b',' + args.split(b',', 2)[2].strip() + b';'
            state.apply(code, args)
            put(data)
            if code in ('IN', 'DF') and overrides:
                put(overrides)
    return written


# Commands that set up the plotter (scaling, rotation, line type, character size, ...). They are
# repeated at the start of a resumed plot, because an IN; undoes them.
SETUP_COMMANDS = {'IP', 'IW', 'SC', 'RO', 'PS', 'LT', 'CA', 'CS', 'SS', 'DI', 'DR', 'SI', 'SR', 'SL',
                  'DT', 'FT', 'PT', 'FS', 'AS', 'VN'}


def resume_preamble(path, offset):
    """What a plotter needs to carry on drawing a file from `offset`.

    Returns (preamble, start): `start` is the beginning of the command at or just before `offset`
    (the sender went back to it rather than risk a gap), and `preamble` is HPGL that puts the
    plotter in the state the file has at `start`: initialised, set up, the pen selected and moved
    there with the pen up (then down again if it was), and in the same absolute or relative mode.
    At the very start of the file nothing is needed. Raises ValueError when nothing is left."""
    state = State()
    setup = []
    start = None
    with open(path, 'rb') as f:
        for first, end, code, args in iter_commands(f):
            if end > offset:
                start = first
                break
            if code == 'IN':
                setup = []
            elif code in SETUP_COMMANDS:
                setup.append(_render(code, args))
            state.apply(code, args)
    if start is None:
        raise ValueError('There is nothing left to plot after that point')
    if start == 0:
        return b'', 0

    out = b'IN;' + b''.join(setup)
    if state.speed:
        out += b'VS%g;' % state.speed
    if state.pen > 0:
        out += b'SP%d;' % state.pen         # never SP0: a hand-fitted pen holder would try to put the pen away
    out += b'PU;PA%d,%d;' % (round(state.x), round(state.y))
    if state.pen_down:
        out += b'PD;'
    if not state.absolute:
        out += b'PR;'
    return out, start


def resume_file(src, dst, offset):
    """Write the rest of `src` from `offset` to `dst`, with the preamble that sets the plotter up.

    Returns (start, preamble_length): `dst` byte n (n >= preamble_length) is `src` byte
    start + n - preamble_length."""
    preamble, start = resume_preamble(src, offset)
    with open(src, 'rb') as f, open(dst, 'wb') as out:
        out.write(preamble)
        f.seek(start)
        shutil.copyfileobj(f, out)
    return start, len(preamble)


def _cache_path(path):
    key = hashlib.sha1(os.path.realpath(path).encode('utf-8')).hexdigest()
    return os.path.join(CACHE_DIR, key + '.json')


def _read_cache(path, units_per_mm):
    stat = os.stat(path)
    stamp = [stat.st_size, stat.st_mtime_ns, units_per_mm]
    try:
        with open(_cache_path(path), 'r', encoding='utf-8') as f:
            found = json.load(f)
        if found.get('stamp') == stamp and found['analysis'].get('version') == VERSION:
            return found['analysis']
    except (OSError, ValueError, KeyError):
        pass
    return None


def cached(path, units_per_mm=UNITS_PER_MM):
    """Is an up to date analysis of the file on disk (or is the file too large to analyse)?"""
    return os.path.getsize(path) > MAX_ANALYSE_BYTES or _read_cache(path, units_per_mm) is not None


def analyze_cached(path, units_per_mm=UNITS_PER_MM):
    """`analyze`, remembering the result on disk until the file changes. Returns None when the
    file is too large to analyse."""
    stat = os.stat(path)
    if stat.st_size > MAX_ANALYSE_BYTES:
        return None
    found = _read_cache(path, units_per_mm)
    if found is not None:
        return found

    cache = _cache_path(path)
    stamp = [stat.st_size, stat.st_mtime_ns, units_per_mm]
    analysis = analyze(path, units_per_mm)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        temp = cache + '.tmp'
        with open(temp, 'w', encoding='utf-8') as f:
            json.dump({'stamp': stamp, 'analysis': analysis}, f)
        os.replace(temp, cache)
    except OSError as e:
        print('Could not cache the HPGL analysis:', repr(e))
    return analysis


def summary(analysis, correction=1.0):
    """The part of an analysis the UI shows, in millimetres and seconds."""
    if analysis is None:
        return None
    mm = analysis['units_per_mm']
    bounds = analysis['bounds']
    return {
        'seconds': analysis['seconds'] * correction,
        'draw_mm': analysis['draw_length'] / mm,
        'travel_mm': analysis['travel_length'] / mm,
        'paths': analysis['paths'],
        'width_mm': (bounds[2] - bounds[0]) / mm if bounds else 0,
        'height_mm': (bounds[3] - bounds[1]) / mm if bounds else 0,
        'pens': [{'pen': p['pen'], 'seconds': p['seconds'] * correction, 'paths': p['paths'],
                  'draw_mm': p['draw_length'] / mm} for p in pens_used(analysis)],
        'unsupported': analysis['unsupported'],
    }
