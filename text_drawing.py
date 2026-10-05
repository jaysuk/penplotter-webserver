"""Text as a drawing: validation and the vpype commands that lay it out on a page.

Pure Python, no vpype, so it is tested everywhere; convert_vpype.create_text runs the commands.
Text reaches vpype on a command line, where it is quoted by shlex, but vpype also evaluates
%expressions% and {properties} in its arguments, so those characters are refused."""
import re
import shlex

# Hershey fonts that look right for Latin text
TEXT_FONTS = ('futural', 'futuram', 'rowmans', 'timesr', 'timesi', 'cursive', 'scripts', 'gothiceng')
TEXT_ALIGNMENTS = ('left', 'center', 'right')
PAGE_SIZES_MM = {'a0': (841, 1189), 'a1': (594, 841), 'a2': (420, 594), 'a3': (297, 420), 'a4': (210, 297)}
LINE_HEIGHT = 1.4          # of the text size
MAX_LENGTH = 300
MAX_LINES = 15
MM_PER_PX = 25.4 / 96
FIT_TOLERANCE_MM = 0.5

FORBIDDEN_RE = re.compile(r'[%{}]')
CONTROL_RE = re.compile(r'[\x00-\x09\x0b-\x1f\x7f]')


class TextError(ValueError):
    """The text cannot be made into a drawing; the message says why, for the user."""


def clean_text(raw):
    """The text with plain line endings and no blank lines at either end. Raises TextError."""
    text = (raw or '').replace('\r\n', '\n').replace('\r', '\n')
    text = '\n'.join(line.rstrip() for line in text.split('\n')).strip('\n')
    if not text.strip():
        raise TextError('There is no text')
    if len(text) > MAX_LENGTH:
        raise TextError('The text is too long (at most {} characters)'.format(MAX_LENGTH))
    if text.count('\n') + 1 > MAX_LINES:
        raise TextError('The text has too many lines (at most {})'.format(MAX_LINES))
    if CONTROL_RE.search(text):
        raise TextError('The text contains control characters')
    if FORBIDDEN_RE.search(text):
        raise TextError('The characters % { } cannot be used')
    return text


def file_name(text):
    """The svg file name for a text: from its first line, safe to use in uploads/."""
    first = next((line for line in text.split('\n') if line.strip()), '')
    slug = re.sub(r'[^a-z0-9]+', '-', first.lower())[:30].strip('-')
    return 'text-{}.svg'.format(slug or 'text')


def commands(text, font, size_mm, page, landscape, margin_mm, align):
    """The vpype commands that draw the text on a page: each line is a line of the drawing, and the
    block is put at the top of the page, aligned, and moved in from the edge by the margin."""
    if font not in TEXT_FONTS or align not in TEXT_ALIGNMENTS or page not in PAGE_SIZES_MM:
        raise TextError('Unknown font, alignment or page size')
    result = ''
    for number, line in enumerate(text.split('\n')):
        if line.strip():
            # `--` so a line that starts with a minus is not taken for an option
            result += ' text -f {} -s {:g}mm -a {} -p 0 {:g}mm -- {}'.format(
                font, size_mm, align, number * size_mm * LINE_HEIGHT, shlex.quote(line))
    if not result:
        raise TextError('There is no text')
    shift = {'left': margin_mm, 'center': 0, 'right': -margin_mm}[align]
    result += ' layout {}--align {} --valign top {} translate -- {:g}mm {:g}mm'.format(
        '-l ' if landscape else '', align, page, shift, margin_mm)
    return result.strip()


def check_fit(bounds_px, page, landscape, margin_mm):
    """Raise TextError if the drawing (left, top, right, bottom in px) leaves the margins."""
    width_mm, height_mm = PAGE_SIZES_MM[page]
    if landscape:
        width_mm, height_mm = height_mm, width_mm
    left, _, right, bottom = (float(v) * MM_PER_PX for v in bounds_px)
    if (left < margin_mm - FIT_TOLERANCE_MM or right > width_mm - margin_mm + FIT_TOLERANCE_MM
            or bottom > height_mm - margin_mm + FIT_TOLERANCE_MM):
        raise TextError('The text does not fit on the page between the margins. Use a smaller size, '
                        'a larger page or shorter lines.')
