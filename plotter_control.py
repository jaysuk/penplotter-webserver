"""HPGL for moving the pen by hand (jog buttons, pen test, tracing the plot area).

Only numbers ever end up in a command, so a request cannot send arbitrary HPGL to the plotter.
These functions are plain Python; sending them is send2serial.run_commands()."""
import re

from hpgl_analysis import UNITS_PER_MM

# [0-9], not \d: \d also matches digits of other scripts, which float() would accept
MM_RE = re.compile(r'-?[0-9]{1,3}(\.[0-9]{1,2})?')
MAX_PEN = 8


def parse_mm(value):
    """A distance in millimetres from a form field, or None if it is not a plain number."""
    if not isinstance(value, str) or not MM_RE.fullmatch(value):
        return None
    return float(value)


def units(mm):
    return round(mm * UNITS_PER_MM)


def jog(dx_mm, dy_mm):
    """Move the pen, lifted, by a distance. Leaves the plotter in absolute mode."""
    return [b'PU;PR%d,%d;PA;' % (units(dx_mm), units(dy_mm))]


def go_to(x_mm, y_mm):
    return [b'PU;PA%d,%d;' % (units(x_mm), units(y_mm))]


def origin():
    return [b'PU;PA0,0;']


def pen_up():
    return [b'PU;']


def pen_down():
    return [b'PD;']


def select_pen(pen):
    """SP0 puts the pen away; 1 to MAX_PEN picks a pen. Returns None for anything else."""
    if not isinstance(pen, int) or not 0 <= pen <= MAX_PEN:
        return None
    return [b'PU;SP%d;' % pen]


def trace_bounds(bounds, draw=False):
    """Go round the rectangle [minx, miny, maxx, maxy] (plotter units), to check where a plot
    will land on the paper. With the pen lifted, unless `draw` is set."""
    minx, miny, maxx, maxy = (round(v) for v in bounds)
    corners = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny)]
    commands = [b'PU;PA%d,%d;' % corners[0]]
    if draw:
        commands.append(b'PD;')
    for x, y in corners[1:]:
        commands.append(b'PA%d,%d;' % (x, y))
    commands.append(b'PU;')
    return commands


def parse_position(reply):
    """The answer to OA; ("x,y,p") as {'x_mm', 'y_mm', 'pen_down'}, or None if it does not parse."""
    try:
        x, y, pen = (int(part) for part in reply.strip().split(','))
    except ValueError:
        return None
    return {'x_mm': round(x / UNITS_PER_MM, 1), 'y_mm': round(y / UNITS_PER_MM, 1), 'pen_down': bool(pen)}
