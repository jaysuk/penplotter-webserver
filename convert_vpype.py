import os
import re
import vpype as vp
from vpype_cli import execute

# Scale and crop the svg to the selected paper size
LANDSCAPE_LAYOUT = ' eval w,h=gprop.vp_page_size crop 0 0 %w% %h% rect -l1000 0 0 %w% %h% layout -l -m  0 '
PORTRAIT_LAYOUT = ' eval w,h=gprop.vp_page_size crop 0 0 %w% %h% rect -l1000 0 0 %w% %h% layout -m  0 '


def convert_file(file, outputsize = 'a4', pageorientation = 'landscape', device = 'hp7475a', speed = '', custom_comand = '', linemerge = '', linesort = '', linesimplify = '', reloop = '', socketio = None):
    """Convert an svg to hpgl with vpype. Returns a message for the UI."""

    def log(message):
        if socketio is not None:
            socketio.emit('status_log', {'data': message})

    if not file:
        return 'File not converted.'

    print('Converting file: ' + file)

    filename, file_extension = os.path.splitext(file)
    vpype_options = ''
    args = ''

    if pageorientation == 'landscape':
        args += LANDSCAPE_LAYOUT + outputsize + ' ldelete 1000'
    else:
        args += PORTRAIT_LAYOUT + outputsize + ' ldelete 1000'

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

    args += ' write --device ' + str(device)

    args += ' --page-size ' + str(outputsize)

    if speed and re.fullmatch(r'\d+(\.\d+)?', str(speed)):
        args += ' -vs ' + str(speed)

    if pageorientation == 'landscape':
        args += ' --landscape'

    outputFile = filename + '-' + outputsize + '-' + pageorientation + vpype_options + '-' + device + '.hpgl'

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
