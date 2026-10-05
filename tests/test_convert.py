"""Runs the real converter, so it needs vpype (skipped when it is not installed)."""
import importlib.util
import os

import pytest

pytest.importorskip('vpype')
pytest.importorskip('vpype_cli')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100">'
       '<path d="M10,10 L90,10 L90,90 L10,90 Z" fill="none" stroke="black"/></svg>')


class SIO:
    def __init__(self):
        self.messages = []

    def emit(self, name, data=None):
        self.messages.append(data['data'])


@pytest.fixture
def convert(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('convert_vpype_real', os.path.join(ROOT, 'convert_vpype.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.chdir(tmp_path)
    (tmp_path / 'uploads').mkdir()
    (tmp_path / 'uploads' / 'a.svg').write_text(SVG)

    def run(*options, file='uploads/a.svg', **keywords):
        sio = SIO()
        # file, outputsize, pageorientation, device, speed, custom_comand, linemerge, linesort, linesimplify, reloop
        defaults = ['a4', 'portrait', 'hp7475a', '', '', '', '', '', '']
        args = list(options) + defaults[len(options):]
        return module.convert_file(file, *args, socketio=sio, **keywords), sio, sorted(os.listdir(tmp_path / 'uploads'))
    return run


def test_converts_to_hpgl(convert, tmp_path):
    result, sio, files = convert()
    assert result.startswith('Exported ')
    assert 'a-a4-portrait-hp7475a.hpgl' in files
    assert (tmp_path / 'uploads' / 'a-a4-portrait-hp7475a.hpgl').read_text().startswith('IN;')
    assert 'File converted.' in sio.messages   # must reach the UI through the passed socketio


def test_optimisation_options_end_up_in_the_file_name(convert):
    _, _, files = convert('a4', 'landscape', 'hp7475a', '', '', 'on', 'on', 'on', 'on')
    assert 'a-a4-landscape-linemrge-linesimplify-reloop-linesort-hp7475a.hpgl' in files


def test_custom_command_is_executed(convert):
    result, _, files = convert('a3', 'landscape', 'hp7475a', '5', 'linemerge --tolerance 0.5mm')
    assert result.startswith('Exported ')
    assert any('linemerge-tolerance-0-5mm' in f for f in files)


@pytest.mark.parametrize('options,file', [
    (('a4', 'portrait', 'hp7475a', '', 'notacommand'), 'uploads/a.svg'),   # vpype rejects the command
    (('a4', 'portrait', 'nodevice'), 'uploads/a.svg'),                      # unknown plotter
    ((), 'uploads/missing.svg'),
])
def test_failures_are_reported_not_raised(convert, options, file):
    result, sio, _ = convert(*options, file=file)
    assert result == 'File not converted.'
    assert 'File not converted.' in sio.messages


# ---- page options -----------------------------------------------------------------------------

CORNER_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="100mm" viewBox="0 0 200 100">'
              '<path d="M10,10 L60,10 L60,20" fill="none" stroke="black"/></svg>')


@pytest.fixture
def drawing(convert, tmp_path):
    """Convert a wide L shape (right, then down) and return the drawing's path in HPGL units."""
    spec = importlib.util.spec_from_file_location('hpgl_analysis_real', os.path.join(ROOT, 'hpgl_analysis.py'))
    hpgl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hpgl)
    (tmp_path / 'uploads' / 'corner.svg').write_text(CORNER_SVG)

    def run(**options):
        result, _, files = convert('a4', 'landscape', file='uploads/corner.svg', **options)
        assert result.startswith('Exported '), result
        name = result[len('Exported '):]
        analysis = hpgl.analyze(name)
        state, points = hpgl.State(), []
        with open(name, 'rb') as f:
            for _, _, code, args in hpgl.iter_commands(f):
                for x0, y0, x1, y1, down in state.apply(code, args):
                    if down:
                        points += [(x0, y0), (x1, y1)]
        (x0, y0), (x1, y1), (x2, y2) = points[0], points[1], points[3]
        return {'name': name, 'width': x1 - x0 if x1 != x0 else y1 - y0, 'first': (x1 - x0, y1 - y0),
                'second': (x2 - x1, y2 - y1), 'bounds': analysis['bounds']}
    return run


def test_rotation_turns_the_drawing(drawing):
    plain = drawing()
    assert plain['first'][0] > 0 and plain['first'][1] == 0 and plain['second'][1] < 0      # right, then down
    clockwise = drawing(rotate=90)
    assert clockwise['first'][1] < 0 and clockwise['first'][0] == 0 and clockwise['second'][0] < 0   # down, then left
    upside_down = drawing(rotate=180)
    assert upside_down['first'][0] < 0 and upside_down['second'][1] > 0                     # left, then up
    counter = drawing(rotate=270)
    assert counter['first'][1] > 0 and counter['second'][0] > 0                             # up, then right


def test_mirroring(drawing):
    assert drawing(mirror_x=True)['first'][0] < 0 and drawing(mirror_x=True)['second'][1] < 0
    assert drawing(mirror_y=True)['first'][0] > 0 and drawing(mirror_y=True)['second'][1] > 0
    both = drawing(mirror_x=True, mirror_y=True)
    assert both['first'][0] < 0 and both['second'][1] > 0


def test_margin_shrinks_the_drawing(drawing):
    full = drawing()['bounds']
    margin = drawing(margin=20)['bounds']
    assert margin[2] - margin[0] < full[2] - full[0]
    # 20 mm on each side of an A4 landscape page leaves 257 mm of the 297 mm
    assert (margin[2] - margin[0]) / (full[2] - full[0]) == pytest.approx(257 / 297, abs=0.01)


def test_page_options_are_in_the_file_name(convert):
    _, _, files = convert('a4', 'portrait', 'hp7475a', '', '', '', '', '', '', margin=12.5, rotate=90,
                          mirror_x=True, mirror_y=True)
    assert 'a-a4-portrait-rotate90-mirrorx-mirrory-margin12-5-hp7475a.hpgl' in files


def test_output_name_matches_what_is_written(convert, tmp_path):
    spec = importlib.util.spec_from_file_location('convert_vpype_name', os.path.join(ROOT, 'convert_vpype.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result, _, files = convert('a3', 'landscape', 'hp7475a', '', '', 'on', '', '', 'on', margin=10, rotate=270)
    name = module.output_name('uploads/a.svg', 'a3', 'landscape', 'hp7475a', '', 'on', '', '', 'on',
                              margin=10, rotate=270)
    assert result == 'Exported ' + name


def test_conversion_can_write_somewhere_else(convert, tmp_path):
    target = tmp_path / 'elsewhere.hpgl'
    result, _, files = convert(output=str(target))
    assert result == 'Exported ' + str(target) and target.read_text().startswith('IN;')
    assert not any(f.endswith('.hpgl') for f in files)       # nothing next to the svg


# ---- text as a drawing ------------------------------------------------------------------------

@pytest.fixture
def text_svg(convert, tmp_path):
    """Make an svg of some text with the real vpype and return (page, bounds) in mm."""
    from vpype_cli import execute
    spec = importlib.util.spec_from_file_location('convert_vpype_text', os.path.join(ROOT, 'convert_vpype.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    mm = 25.4 / 96

    def make(text, **options):
        module.create_text(text, output='uploads/t.svg', **options)
        doc = execute('read uploads/t.svg')
        return tuple(round(float(v) * mm) for v in doc.page_size), tuple(round(float(v) * mm, 1) for v in doc.bounds())
    return make, module


def test_text_is_laid_out_inside_the_margins(text_svg):
    make, _ = text_svg
    page, (left, top, right, bottom) = make('Hello world', size_mm=20)
    assert page == (210, 297) and (left, top) == (15.0, 15.0) and right < 195


def test_text_alignment(text_svg):
    make, _ = text_svg
    _, (left, _, right, _) = make('Right', align='right', size_mm=15)
    assert right == 195.0
    _, (left, _, right, _) = make('Centre', align='center', size_mm=15)
    assert (left + right) / 2 == pytest.approx(105, abs=0.2)


def test_text_page_and_orientation(text_svg):
    make, _ = text_svg
    assert make('Wide', page='a3', landscape=True)[0] == (420, 297)
    assert make('Tall', page='a3', landscape=False)[0] == (297, 420)


def test_several_lines(text_svg):
    make, _ = text_svg
    _, one = make('One', size_mm=10)
    _, three = make('One\nTwo\nThree', size_mm=10)
    assert three[3] - three[1] > 2 * (one[3] - one[1])


def test_text_that_is_too_wide_is_refused(text_svg):
    make, module = text_svg
    with pytest.raises(module.text_drawing.TextError):
        make('x' * 60, size_mm=30)


def test_awkward_text_reaches_vpype_unharmed(text_svg):
    make, _ = text_svg
    make('say "hi" $HOME `x` ; & | < > \\ \'q\' -minus', size_mm=8)


def test_the_text_svg_converts_to_hpgl(text_svg, convert):
    make, _ = text_svg
    make('Plot me', size_mm=20)
    result, _, files = convert('a4', 'portrait', file='uploads/t.svg')
    assert result.startswith('Exported ') and any(f.endswith('.hpgl') for f in files)
