"""vpype plugins: the list for the convert dialog and the check on custom commands. Plugins are faked
with real click commands behind fake entry points, so vpype itself is not needed."""
import click
import pytest

CONVERT = {'file': 'a.svg', 'outputsize': 'a4', 'pageorientation': 'landscape', 'device': 'hp7475a'}


@click.command(name='spiral')
@click.option('-t', '--turns', type=int, default=3, help='How many turns.\n\nMore text.')
@click.option('--fill', is_flag=True)
@click.argument('radius', type=float)
def spiral(turns, fill, radius):
    """Draw a spiral."""


@click.command(name='imageflow')
@click.argument('image', type=click.Path())
def imageflow(image):
    """Make lines from an image."""


@click.command(name='dump')
@click.option('--to', type=click.File('w'))
def dump(to):
    """Write something."""


class Dist:
    def __init__(self, name, version):
        self.metadata = {'Name': name}
        self.version = version


class EntryPoint:
    def __init__(self, name, command, dist, value='pkg:cmd'):
        self.name, self.command, self.dist, self.value = name, command, dist, value

    def load(self):
        if isinstance(self.command, Exception):
            raise self.command
        return self.command


@pytest.fixture
def plugins(app, monkeypatch):
    shapes = Dist('vpype-shapes', '1.2')
    entry_points = [
        EntryPoint('imageflow', imageflow, Dist('vpype-flow', '0.4')),
        EntryPoint('spiral', spiral, shapes),
        EntryPoint('dump', dump, shapes),
        EntryPoint('broken', ImportError('no module named secret_path'), Dist('vpype-broken', '0.1')),
    ]
    monkeypatch.setattr(app.main.vpype_plugins, '_entry_points', lambda: entry_points)
    (app.dir / 'uploads' / 'a.svg').write_text('<svg/>')
    return app.main.vpype_plugins


def test_plugins_are_listed_by_package(plugins):
    listed = plugins.installed()
    assert [p['name'] for p in listed] == ['vpype-broken', 'vpype-flow', 'vpype-shapes']
    shapes = listed[2]
    assert shapes['version'] == '1.2' and [c['name'] for c in shapes['commands']] == ['dump', 'spiral']


def test_commands_describe_their_options(plugins):
    command = [c for p in plugins.installed() for c in p['commands'] if c['name'] == 'spiral'][0]
    assert command['help'] == 'Draw a spiral.' and not command['file']
    turns, fill, radius = command['params']
    assert turns['flags'] == '-t / --turns' and turns['default'] == 3 and turns['help'] == 'How many turns.'
    assert fill['type'] == 'flag' and radius['flags'] == 'RADIUS' and radius['required']


def test_commands_with_files_are_marked(plugins):
    assert plugins.file_commands() == {'imageflow', 'dump'}


def test_a_plugin_that_cannot_load_is_listed_without_details(plugins):
    broken = plugins.installed()[0]
    assert broken['commands'] == [] and broken['error'] == 'Could not be loaded (ImportError)'
    assert 'secret_path' not in str(plugins.installed())


def test_listing_survives_an_entry_point_scan_that_finds_nothing(app, monkeypatch):
    monkeypatch.setattr(app.main.vpype_plugins, '_entry_points', lambda: [])
    assert app.main.vpype_plugins.installed() == []


def test_the_route_lists_the_plugins(client, plugins):
    data = client.get('/vpype_plugins').get_json()
    assert [p['name'] for p in data['plugins']] == ['vpype-broken', 'vpype-flow', 'vpype-shapes']
    assert data['install'].endswith(' -m pip install ')


def test_plugin_commands_can_be_used_as_custom_commands(client, plugins, app):
    r = client.post('/start_conversion', data={**CONVERT, 'command_input': 'spiral --turns 5 10'})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert app.convert_stub.calls[-1][0][5] == 'spiral --turns 5 10'


@pytest.mark.parametrize('command', ['imageflow cat', 'linemerge IMAGEFLOW x', 'dump --to out', 'dump'])
def test_plugin_commands_that_take_files_are_refused(client, plugins, command):
    r = client.post('/start_conversion', data={**CONVERT, 'command_input': command})
    assert r.status_code == 400 and 'reads or writes files' in r.get_data(as_text=True)


def test_the_blocked_commands_stay_blocked(client, plugins):
    r = client.post('/start_conversion', data={**CONVERT, 'command_input': 'eval x'})
    assert r.status_code == 400 and 'not allowed' in r.get_data(as_text=True)
