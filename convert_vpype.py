import os
import re
import vpype as vp
from vpype_cli import execute

# Scale and crop the svg to the selected paper size
LANDSCAPE_LAYOUT = ' eval w,h=gprop.vp_page_size crop 0 0 %w% %h% rect -l1000 0 0 %w% %h% '
PORTRAIT_LAYOUT = ' eval w,h=gprop.vp_page_size crop 0 0 %w% %h% rect -l1000 0 0 %w% %h% '

# Turning the page: vpype's pagerotate turns it 90 degrees counter-clockwise
ROTATIONS = {
    0: '',
    90: ' pagerotate --clockwise',
    180: ' pagerotate pagerotate',
    270: ' pagerotate',
}


def format_margin(margin):
    """A margin in millimetres as vpype wants it: '0' (fit to the page) or e.g. '12.5mm'."""
    margin = float(margin or 0)
    if margin <= 0:
        return '0'
    return '{:g}mm'.format(margin)


def _build(outputsize, pageorientation, custom_comand, linemerge, linesort, linesimplify, reloop,
           margin, rotate, mirror_x, mirror_y):
    """The vpype commands between reading and writing, and the part of the file name that says
    which options were used."""
    # The page is added as a rectangle (layer 1000) so layout fits the page, not just the drawing
    args = PORTRAIT_LAYOUT if pageorientation != 'landscape' else LANDSCAPE_LAYOUT
    vpype_options = ''

    # Turn and flip the whole page, before it is fitted to the paper
    rotate = int(rotate or 0)
    if rotate:
        args += ROTATIONS[rotate]
        vpype_options += '-rotate{}'.format(rotate)
    if mirror_x:
        args += ' scale -- -1 1'
        vpype_options += '-mirrorx'
    if mirror_y:
        args += ' scale -- 1 -1'
        vpype_options += '-mirrory'

    args += ' layout ' + ('-l ' if pageorientation == 'landscape' else '') + '-m ' + format_margin(margin) + ' '
    args += outputsize + ' ldelete 1000'
    if float(margin or 0) > 0:
        vpype_options += '-margin{}'.format(re.sub(r'[^0-9]+', '-', '{:g}'.format(float(margin))))

    if custom_comand:
        # Custom vpype commands replace the optimisation options
        args += ' ' + custom_comand
        vpype_options += '-' + re.sub(r'[^A-Za-z0-9]+', '-', custom_comand).strip('-')
    else:
        if linemerge:
            args += ' linemerge --tolerance 0.2mm'
            vpype_options += '-linemrge'

        if linesimplify:
            args += ' linesimplify --tolerance 0.1mm'
            vpype_options += '-linesimplify'

        if reloop:
            args += ' reloop'
            vpype_options += '-reloop'

        if linesort:
            args += ' linesort'
            vpype_options += '-linesort'

    return args, vpype_options


def output_name(file, outputsize='a4', pageorientation='landscape', device='hp7475a', custom_comand='',
                linemerge='', linesort='', linesimplify='', reloop='', margin=0, rotate=0,
                mirror_x=False, mirror_y=False):
    """The HPGL file name that converting `file` with these options produces."""
    filename, _ = os.path.splitext(file)
    _, vpype_options = _build(outputsize, pageorientation, custom_comand, linemerge, linesort,
                              linesimplify, reloop, margin, rotate, mirror_x, mirror_y)
    return filename + '-' + outputsize + '-' + pageorientation + vpype_options + '-' + device + '.hpgl'


def convert_file(file, outputsize = 'a4', pageorientation = 'landscape', device = 'hp7475a', speed = '', custom_comand = '', linemerge = '', linesort = '', linesimplify = '', reloop = '', socketio = None,
                 margin = 0, rotate = 0, mirror_x = False, mirror_y = False, output = None):
    """Convert an svg to hpgl with vpype. Returns a message for the UI.

    The result is written next to the svg, under a name that says which options were used, or to
    `output` when that is given."""

    def log(message):
        if socketio is not None:
            socketio.emit('status_log', {'data': message})

    if not file:
        return 'File not converted.'

    print('Converting file: ' + file)

    args, _ = _build(outputsize, pageorientation, custom_comand, linemerge, linesort, linesimplify,
                     reloop, margin, rotate, mirror_x, mirror_y)
    outputFile = output or output_name(file, outputsize, pageorientation, device, custom_comand, linemerge,
                                       linesort, linesimplify, reloop, margin, rotate, mirror_x, mirror_y)

    args += ' write --device ' + str(device)

    args += ' --page-size ' + str(outputsize)

    if speed and re.fullmatch(r'\d+(\.\d+)?', str(speed)):
        args += ' -vs ' + str(speed)

    if pageorientation == 'landscape':
        args += ' --landscape'

    args += ' --center'
    args += ' "' + os.path.join(os.getcwd(), str(outputFile)) + '"'

    try:
        doc = execute('read "' + os.path.join(os.getcwd(), str(file)) + '"')
        execute(args, doc)
    except (Exception, SystemExit) as e:
        print('vpype error: ' + repr(e))
        log('File not converted.')
        log('Error: ' + str(e))
        return 'File not converted.'

    log('File converted.')
    return 'Exported ' + str(outputFile)
