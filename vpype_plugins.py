"""The vpype plugins that are installed, and what their commands take.

vpype finds plugins through the `vpype.plugins` entry point group, so a plugin is installed with
pip into the same environment as the server (and the server restarted). This module only looks:
it lists the commands for the convert dialog and tells the validation which of them take a file,
because a custom command is run in the server's own folder and must not read or write files there.
"""
import importlib.metadata
import sys

import click

GROUP = 'vpype.plugins'
# click's name for a parameter that is a file or a path (click.Path, click.File, vpype's own types)
FILE_TYPE_NAMES = {'path', 'file', 'filename'}
HELP_LENGTH = 300


def _entry_points():
    found = importlib.metadata.entry_points()
    if hasattr(found, 'select'):
        return list(found.select(group=GROUP))
    return list(found.get(GROUP, []))      # Python 3.9


def _distribution(entry_point):
    dist = getattr(entry_point, 'dist', None)
    if dist is not None:
        return dist.metadata['Name'] or entry_point.name, dist.version
    return entry_point.value.split(':')[0].split('.')[0], ''


def takes_file(param):
    return isinstance(param.type, (click.Path, click.File)) or param.type.name in FILE_TYPE_NAMES


def _plain(value):
    """A default that can go into JSON (click 8.3 uses a sentinel object for 'no default')."""
    return value if isinstance(value, (bool, int, float, str)) else None


def _first_paragraph(text):
    text = ' '.join((text or '').strip().split('\n\n')[0].split())
    return text if len(text) <= HELP_LENGTH else text[:HELP_LENGTH - 1].rstrip() + '…'


def _describe_param(param):
    if isinstance(param, click.Option):
        flags = ' / '.join(param.opts + param.secondary_opts)
    else:
        flags = param.name.upper()
    return {
        'flags': flags,
        'type': 'flag' if getattr(param, 'is_flag', False) else param.type.name,
        'required': bool(param.required),
        'default': None if param.required else _plain(param.default),
        'help': _first_paragraph(getattr(param, 'help', '')),
        'file': takes_file(param),
    }


def _describe_command(command):
    params = [_describe_param(p) for p in command.params]
    return {
        'name': command.name,
        'help': _first_paragraph(command.help),
        'params': params,
        'file': any(p['file'] for p in params),
    }


def installed():
    """The installed plugins, grouped by the package they come from:
    [{'name', 'version', 'commands': [{'name', 'help', 'params', 'file'}], 'error'}].
    A plugin that cannot be loaded is listed with an error (vpype skips it too)."""
    plugins = {}
    for entry_point in _entry_points():
        name, version = _distribution(entry_point)
        plugin = plugins.setdefault(name, {'name': name, 'version': version, 'commands': [], 'error': None})
        try:
            plugin['commands'].append(_describe_command(entry_point.load()))
        except Exception as e:      # a plugin is third party code: whatever it raises, keep listing
            plugin['error'] = 'Could not be loaded ({})'.format(type(e).__name__)
    for plugin in plugins.values():
        plugin['commands'].sort(key=lambda command: command['name'])
    return sorted(plugins.values(), key=lambda plugin: plugin['name'].lower())


def file_commands():
    """Lower case names of the plugin commands that take a file or a path."""
    return {command['name'].lower() for plugin in installed() for command in plugin['commands']
            if command['file']}


def install_command():
    """What to run on the machine to install a plugin into the environment the server runs in."""
    return '{} -m pip install '.format(sys.executable)
