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

    def run(*options, file='uploads/a.svg'):
        sio = SIO()
        # file, outputsize, pageorientation, device, speed, custom_comand, linemerge, linesort, linesimplify, reloop
        defaults = ['a4', 'portrait', 'hp7475a', '', '', '', '', '', '']
        args = list(options) + defaults[len(options):]
        return module.convert_file(file, *args, socketio=sio), sio, sorted(os.listdir(tmp_path / 'uploads'))
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
