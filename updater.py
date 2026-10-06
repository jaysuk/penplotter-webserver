"""Knowing that a newer version exists, and starting the installer's update (no Flask here).

The version is the text in the `VERSION` file. The server compares it with the `VERSION` file on the
GitHub branch the install came from (read from the install's own git remote, never from a request).
The update itself is the installer, `install.sh`, downloaded from that same branch and run in a
transient systemd unit: it stops the web plotter's own service, so it cannot be a child of it.
"""
import os
import re
import shutil
import subprocess
import threading
import time

import requests

import changelog

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(HERE, 'VERSION')

DEFAULT_REPO = 'jaysuk/penplotter-webserver'
DEFAULT_BRANCH = 'PiPlot'
VERSION_RE = re.compile(r'[0-9]{1,4}(\.[0-9]{1,4}){0,3}')
REPO_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}')
BRANCH_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,99}')
GITHUB_URL_RE = re.compile(r'(?:https://(?:[^@/]+@)?|git@|ssh://git@)github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?')

REQUEST_TIMEOUT = 10
MAX_BYTES = 1024 * 1024         # the installer is about 20 KB
CHECK_EVERY_S = 24 * 3600
FIRST_CHECK_S = 120             # let the network come up after a boot
TICK_S = 3600                   # how often the background thread looks at the settings again
STALE_S = 2 * 3600              # an update that has said "running" for longer than this has died

UNIT = 'webplotter-update'
INSTALL_DIR_NAME = 'webplotter'     # install.sh puts the web plotter in $HOME/webplotter

lock = threading.Lock()
state = {'latest': None, 'checked_at': None, 'error': None, 'entries': []}


# ---- versions ---------------------------------------------------------------------------------

def parse(text):
    """'1.2.3' -> (1, 2, 3), or None when it is not a version."""
    text = (text or '').strip()
    if not VERSION_RE.fullmatch(text):
        return None
    return tuple(int(part) for part in text.split('.'))


def is_newer(latest, current):
    a, b = parse(latest), parse(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def current_version():
    try:
        with open(VERSION_FILE) as f:
            text = f.read(64).strip()
    except OSError:
        return None
    return text if parse(text) else None


# ---- where this install came from -------------------------------------------------------------

def _git(*args):
    try:
        done = subprocess.run(['git', '-C', HERE] + list(args), stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, text=True)
    except (OSError, subprocess.SubprocessError):
        return ''
    return (done.stdout or '').strip() if done.returncode == 0 else ''


def source():
    """(owner/name, branch) of the GitHub repository this install was cloned from. A copy that is not a git
    checkout, or has another host as its remote, follows the project's own branch."""
    match = GITHUB_URL_RE.fullmatch(_git('config', '--get', 'remote.origin.url'))
    repo = '{}/{}'.format(*match.groups()) if match else DEFAULT_REPO
    branch = _git('rev-parse', '--abbrev-ref', 'HEAD')
    if not REPO_RE.fullmatch(repo):
        repo = DEFAULT_REPO
    if not BRANCH_RE.fullmatch(branch) or branch == 'HEAD':
        branch = DEFAULT_BRANCH
    return repo, branch


_source = {'at': 0.0, 'result': (DEFAULT_REPO, DEFAULT_BRANCH)}


def cached_source(max_age=600):
    """source() runs git, so the page's polling reuses the last answer for a while."""
    if time.time() - _source['at'] > max_age:
        _source['result'] = source()
        _source['at'] = time.time()
    return _source['result']


def raw_url(path):
    repo, branch = source()
    return 'https://raw.githubusercontent.com/{}/{}/{}'.format(repo, branch, path)


# ---- the check --------------------------------------------------------------------------------

def _get(path, limit):
    """The first `limit` bytes of a file of the repository. Raises RuntimeError with something to show."""
    try:
        response = requests.get(raw_url(path), timeout=REQUEST_TIMEOUT, stream=True)
        if response.status_code != 200:
            raise RuntimeError('GitHub answered {}'.format(response.status_code))
        return response.raw.read(limit, decode_content=True)
    except requests.exceptions.RequestException as e:
        raise RuntimeError(type(e).__name__)


def check():
    """Ask GitHub for the newest version. Returns the new state; a failure is recorded, not raised."""
    latest, error = None, None
    try:
        text = _get('VERSION', 65).decode('ascii', 'replace').strip()
        if parse(text):
            latest = text
        else:
            error = 'The version on GitHub could not be read'
    except RuntimeError as e:
        error = str(e)
    entries = None
    if latest:
        try:        # what changed, so the page can say so before anyone updates; the version alone is enough without it
            entries = changelog.parse(_get('CHANGELOG.md', changelog.MAX_BYTES).decode('utf-8', 'replace'))
        except RuntimeError:
            entries = None
    with lock:
        state['checked_at'] = time.time()
        state['error'] = error
        if latest:
            state['latest'] = latest
            state['entries'] = entries or []
        return dict(state)


# ---- what the installer leaves behind, and what is allowed to start it ------------------------

def home():
    return os.path.expanduser('~')


def status_path():
    return os.path.join(home(), 'webplotter-update.status')


def log_path():
    return os.path.join(home(), 'webplotter-update.log')


def installer_path():
    return os.path.join(home(), 'webplotter-update.sh')


def progress():
    """{'state': 'idle'|'running'|'done'|'failed', 'log': last lines}. The wrapper that runs the installer
    writes `running`, then `done` or `failed <code>`."""
    path = status_path()
    try:
        with open(path) as f:
            text = f.read(100).strip()
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return {'state': 'idle', 'log': []}
    if text.startswith('running'):
        result = 'running' if age < STALE_S else 'failed'
    elif text.startswith('done'):
        result = 'done'
    elif text.startswith('failed'):
        result = 'failed'
    else:
        result = 'idle'
    return {'state': result, 'log': log_tail()}


def log_tail(lines=12):
    try:
        with open(log_path(), 'rb') as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(f.tell() - 4000, 0))
            text = f.read().decode('utf-8', 'replace')
    except OSError:
        return []
    return [line.rstrip() for line in text.splitlines() if line.strip()][-lines:]


def can_update():
    """(True, '') or (False, why not)."""
    if os.name != 'posix':
        return False, 'Updating from the page only works on the Raspberry Pi'
    expected = os.path.realpath(os.path.join(home(), INSTALL_DIR_NAME))
    if os.path.realpath(HERE) != expected:
        return False, 'This copy is not the one the installer set up ({}), so it has to be updated by hand'.format(expected)
    if not os.path.isdir('/run/systemd/system') or not shutil.which('systemd-run'):
        return False, 'systemd is not running here'
    try:
        allowed = subprocess.run(['sudo', '-n', 'true'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        allowed = False
    if not allowed:
        return False, ('The user running the web plotter needs passwordless sudo to update itself. '
                       'Run the installer again over SSH and answer yes when it asks, or update by hand')
    return True, ''


def download_installer():
    """Fetch install.sh from the same branch and keep it in the home folder. Raises RuntimeError."""
    try:
        data = _get('install.sh', MAX_BYTES + 1)
    except RuntimeError as e:
        raise RuntimeError('The installer could not be downloaded ({})'.format(e))
    if len(data) > MAX_BYTES or not data.startswith(b'#!/bin/bash'):
        raise RuntimeError('What was downloaded is not the installer')
    path = installer_path()
    with open(path, 'wb') as f:
        f.write(data)
    return path


# One argument to bash; everything variable comes in through the environment
WRAPPER = ('echo running > "$WEBPLOTTER_UPDATE_STATUS"; '
           'bash "$WEBPLOTTER_UPDATE_SCRIPT"; rc=$?; '
           'if [ "$rc" -eq 0 ]; then echo done > "$WEBPLOTTER_UPDATE_STATUS"; '
           'else echo "failed $rc" > "$WEBPLOTTER_UPDATE_STATUS"; fi')


def start():
    """Run the installer's update in its own systemd unit. Raises RuntimeError with something to show the user."""
    ok, why = can_update()
    if not ok:
        raise RuntimeError(why)
    if progress()['state'] == 'running':
        raise RuntimeError('An update is already running')
    repo, branch = source()
    script = download_installer()
    try:
        os.remove(status_path())
    except OSError:
        pass
    command = ['sudo', '-n', 'systemd-run', '--unit=' + UNIT, '--collect', '--quiet',
               '--uid={}'.format(os.getuid()), '--gid={}'.format(os.getgid()),
               '--working-directory=' + home(),
               '--setenv=HOME=' + home(),
               '--setenv=WEBPLOTTER_NO_REBOOT=1',          # the installer restarts the service itself
               '--setenv=WEBPLOTTER_REPO=https://github.com/{}.git'.format(repo),
               '--setenv=WEBPLOTTER_BRANCH=' + branch,
               '--setenv=WEBPLOTTER_LOG=' + log_path(),
               '--setenv=WEBPLOTTER_UPDATE_SCRIPT=' + script,
               '--setenv=WEBPLOTTER_UPDATE_STATUS=' + status_path(),
               '/bin/bash', '-c', WRAPPER]
    try:
        done = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=30, text=True)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError('The update could not be started ({})'.format(type(e).__name__))
    if done.returncode != 0:
        print('systemd-run failed:', done.stdout.strip()[-300:])
        raise RuntimeError('The update could not be started (systemd-run failed)')


# ---- what has been announced ------------------------------------------------------------------

def read_announced(path):
    try:
        with open(path) as f:
            text = f.read(64).strip()
    except OSError:
        return None
    return text if parse(text) else None


def write_announced(path, version):
    try:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w') as f:
            f.write(version + '\n')
    except OSError as e:
        print('Could not remember the announced version:', type(e).__name__)


_can = {'at': 0.0, 'result': (False, '')}


def can_update_cached(max_age=60):
    """can_update() runs sudo, so the page's polling reuses the last answer for a while."""
    if time.time() - _can['at'] > max_age:
        _can['result'] = can_update()
        _can['at'] = time.time()
    return _can['result']


def summary():
    """What /api/status shows: no subprocesses, so it is cheap to ask for."""
    with lock:
        latest = state['latest']
    current = current_version()
    return {'available': is_newer(latest, current), 'latest': latest}


def status(cached=False):
    with lock:
        latest, checked_at, error, entries = state['latest'], state['checked_at'], state['error'], state['entries']
    current = current_version()
    repo, branch = cached_source() if cached else source()
    ok, why = can_update_cached() if cached else can_update()
    return {'current': current, 'latest': latest, 'available': is_newer(latest, current),
            'checked_at': checked_at, 'error': error, 'repo': repo, 'branch': branch,
            'changes': changelog.between(entries, current, latest) if latest and current else [],
            'can_update': ok, 'why_not': why, 'progress': progress()}
