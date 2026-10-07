"""Every third-party module the app imports must be in requirements.txt: the installer installs that file
and nothing else, so a missing line would only show up on a user's Pi."""
import ast
import glob
import importlib.util
import os
import re
import sysconfig

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# What a module is called when it is imported, by the name of the requirement that provides it
IMPORT_NAMES = {
    'pyserial': {'serial'},
    'requests': {'requests'},
    'flask': {'flask', 'werkzeug', 'jinja2', 'markupsafe', 'itsdangerous', 'click'},
    'flask-socketio': {'flask_socketio', 'socketio', 'engineio'},
    'vpype': {'vpype', 'vpype_cli', 'click', 'tomli'},      # tomli: vpype needs it on Python < 3.11
    'paho-mqtt': {'paho'},
    'gpiozero': {'gpiozero'},
}


def requirement_names():
    names = set()
    with open(os.path.join(ROOT, 'requirements.txt'), encoding='utf-8') as f:
        for line in f:
            line = line.split('#')[0].strip()
            if line:
                names.add(re.split(r'[<>=!~\[; ]', line, maxsplit=1)[0].lower().replace('_', '-'))
    return names


def is_standard_library(name):
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return False
    if spec is None:
        return False
    if spec.origin in ('built-in', 'frozen'):
        return True
    origin = (spec.origin or '').replace('\\', '/')
    stdlib = sysconfig.get_paths()['stdlib'].replace('\\', '/')
    return origin.startswith(stdlib) and 'site-packages' not in origin


def app_modules():
    return {os.path.splitext(os.path.basename(path))[0] for path in glob.glob(os.path.join(ROOT, '*.py'))}


def imported_names():
    """{top level module: the file that imports it} for every import in the app's own modules,
    including the ones made inside functions."""
    found = {}
    for path in sorted(glob.glob(os.path.join(ROOT, '*.py'))):
        with open(path, encoding='utf-8') as f:
            tree = ast.parse(f.read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.setdefault(alias.name.split('.')[0], os.path.basename(path))
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.setdefault(node.module.split('.')[0], os.path.basename(path))
    return found


def test_every_third_party_import_is_a_requirement():
    provided = set()
    for requirement in requirement_names():
        provided |= IMPORT_NAMES.get(requirement, {requirement.replace('-', '_')})
    local = app_modules()
    missing = {name: where for name, where in imported_names().items()
               if name not in local and not is_standard_library(name) and name not in provided}
    assert not missing, 'not in requirements.txt (or IMPORT_NAMES here): {}'.format(missing)


def test_the_requirements_are_all_used():
    """A requirement nothing imports is dead weight on a Pi Zero (vpype is imported by vpype_cli)."""
    imported = set(imported_names())
    unused = [requirement for requirement in requirement_names()
              if not (IMPORT_NAMES.get(requirement, {requirement.replace('-', '_')}) & imported)]
    assert not unused, unused
