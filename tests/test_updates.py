"""Knowing a newer version is out, telling people, and starting the installer's update."""
import os
import time
import types

import pytest

from test_notifications import sent, save, TELEGRAM      # noqa: F401  (sent is a fixture)
from test_routes import PLOT, slow_plot, wait_for        # noqa: F401  (slow_plot is a fixture)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALL_CHANNELS = dict(TELEGRAM, webhook_url='https://example.org/hook', mqtt_host='broker.local')


@pytest.fixture
def up(app, monkeypatch, tmp_path):
    """The updater with GitHub faked, an empty home folder, and a forgotten past."""
    updater = app.updater
    github = types.SimpleNamespace(version='1.0.1', error=None, installer=b'#!/bin/bash\necho hi\n', asked=[])

    def get(path, limit):
        github.asked.append(path)
        if github.error:
            raise RuntimeError(github.error)
        return (github.version.encode() if path == 'VERSION' else github.installer)[:limit]

    monkeypatch.setattr(updater, '_get', get)
    monkeypatch.setattr(updater, '_git', lambda *args: '')
    monkeypatch.setattr(updater, 'home', lambda: str(tmp_path))
    monkeypatch.setattr(updater, 'current_version', lambda: '1.0.0')
    updater.state.update(latest=None, checked_at=None, error=None)
    updater._can['at'] = 0.0
    updater._source['at'] = 0.0
    monkeypatch.setattr(updater, 'can_update', lambda: (True, ''))
    monkeypatch.setattr(os, 'getuid', lambda: 1000, raising=False)      # not on Windows
    monkeypatch.setattr(os, 'getgid', lambda: 1001, raising=False)
    github.home = tmp_path
    return github


# ---- versions ---------------------------------------------------------------------------------

def test_the_version_file_is_a_version():
    with open(os.path.join(ROOT, 'VERSION')) as f:
        text = f.read().strip()
    import re
    assert re.fullmatch(r'[0-9]{1,4}(\.[0-9]{1,4}){0,3}', text), text


@pytest.mark.parametrize('latest,current,newer', [
    ('1.0.1', '1.0.0', True), ('1.1', '1.0.9', True), ('2.0.0', '1.99.99', True), ('1.10.0', '1.9.0', True),
    ('1.0.0', '1.0.0', False), ('1.0', '1.0.0', False), ('0.9.0', '1.0.0', False),
    ('x', '1.0.0', False), ('1.0.0', None, False), (None, '1.0.0', False), ('1.0.0\n', '0.9', True),
])
def test_versions_compare_by_number_not_by_text(app, latest, current, newer):
    assert app.updater.is_newer(latest, current) is newer


@pytest.mark.parametrize('url,branch,expected', [
    ('https://github.com/jaysuk/penplotter-webserver.git', 'PiPlot', ('jaysuk/penplotter-webserver', 'PiPlot')),
    ('git@github.com:someone/fork.git', 'feature/x', ('someone/fork', 'feature/x')),
    ('https://user:token@github.com/someone/fork', 'main', ('someone/fork', 'main')),
    ('https://example.org/me/fork.git', 'main', ('jaysuk/penplotter-webserver', 'main')),     # not GitHub
    ('', '', ('jaysuk/penplotter-webserver', 'PiPlot')),                                       # not a checkout
    ('https://github.com/a/b.git', 'HEAD', ('a/b', 'PiPlot')),                                 # detached
    ('https://github.com/a/b.git', 'x; rm -rf /', ('a/b', 'PiPlot')),
])
def test_the_source_is_read_from_the_installs_own_remote(app, monkeypatch, url, branch, expected):
    monkeypatch.setattr(app.updater, '_git', lambda *args: url if args[0] == 'config' else branch)
    assert app.updater.source() == expected


# ---- the check --------------------------------------------------------------------------------

def test_a_check_records_the_latest_version(app, up):
    app.updater.check()
    info = app.updater.status()
    assert info['latest'] == '1.0.1' and info['available'] is True and info['error'] is None
    assert info['current'] == '1.0.0' and info['checked_at']
    assert up.asked == ['VERSION', 'CHANGELOG.md']


def test_a_failed_check_keeps_what_was_known(app, up):
    app.updater.check()
    up.error = 'ConnectionError'
    app.updater.check()
    info = app.updater.status()
    assert info['error'] == 'ConnectionError' and info['latest'] == '1.0.1'
    up.error, up.version = None, 'not a version'
    app.updater.check()
    assert app.updater.status()['error'] == 'The version on GitHub could not be read'


def test_the_same_version_is_not_an_update(app, up):
    up.version = '1.0.0'
    app.updater.check()
    assert app.updater.status()['available'] is False


# ---- announcing it ----------------------------------------------------------------------------

def test_a_new_version_goes_to_telegram_the_webhook_and_mqtt(app, client, up, sent):
    save(client, **ALL_CHANNELS)
    app.main.check_for_update()

    assert len(sent.telegram) == 1 and '1.0.1' in sent.telegram[0] and '1.0.0' in sent.telegram[0]
    [(url, payload, _)] = sent.webhook
    assert payload['event'] == 'update' and payload['version'] == '1.0.1' and payload['current'] == '1.0.0'
    [(topic, body, _)] = sent.mqtt
    assert topic == 'webplotter/update' and '"version": "1.0.1"' in body


def test_a_version_is_announced_once(app, client, up, sent):
    save(client, **ALL_CHANNELS)
    app.main.check_for_update()
    app.main.check_for_update()
    assert len(sent.telegram) == 1
    up.version = '1.0.2'
    app.main.check_for_update()
    assert len(sent.telegram) == 2 and '1.0.2' in sent.telegram[1]


def test_nothing_is_announced_when_there_is_nothing_new(app, client, up, sent):
    save(client, **ALL_CHANNELS)
    up.version = '1.0.0'
    app.main.check_for_update()
    assert sent.telegram == [] and sent.webhook == [] and sent.mqtt == []


def test_the_announcement_can_be_switched_off(app, client, up, sent):
    save(client, **ALL_CHANNELS, notify_update='false')
    app.main.check_for_update()
    assert sent.telegram == [] and sent.webhook == [] and sent.mqtt == []
    assert app.updater.status()['available'] is True            # the page still shows it
    save(client, notify_update='true')
    app.main.check_for_update()
    assert len(sent.telegram) == 1                              # switched on later: announced then


def test_the_announcement_and_the_check_are_on_by_default(app, client, up, sent):
    settings = client.get('/save_configfile').get_json()
    assert settings['notify_update'] == 'true' and settings['update_check'] == 'true'
    assert app.main.update_check_enabled() is True
    assert app.main.notification.enabled('update') is True
    save(client, update_check='false')
    assert app.main.update_check_enabled() is False


def test_a_hand_edited_check_setting_falls_back_to_on(app):
    app.main.config.set('updates', 'update_check', 'maybe')
    assert app.main.update_check_enabled() is True


# ---- the routes -------------------------------------------------------------------------------

def test_status_route(app, client, up):
    info = client.get('/update/status').get_json()
    assert info['current'] == '1.0.0' and info['latest'] is None and info['available'] is False
    assert info['can_update'] is True and info['progress']['state'] == 'idle'
    assert client.post('/update/check').get_json()['available'] is True
    assert client.get('/update/status').get_json()['latest'] == '1.0.1'


def test_the_api_status_shows_the_version(app, client, up):
    app.updater.check()
    status = client.get('/api/status').get_json()
    assert status['version'] == '1.0.0'
    assert status['update'] == {'available': True, 'latest': '1.0.1'}


def test_update_routes_are_post_only_where_they_change_something(client):
    assert client.get('/update/start').status_code == 405
    assert client.get('/update/check').status_code == 405


def test_the_page_has_the_update_dialog(client):
    page = client.get('/').get_data(as_text=True)
    for needle in ('id="modal-update"', 'id="updateChip"', 'class="uk-button uk-button-primary startUpdate"'):
        assert needle in page


def test_starting_an_update_holds_the_plot_lock_until_it_is_over(app, client, up, monkeypatch):
    started = []
    monkeypatch.setattr(app.updater, 'start', lambda: started.append(True))
    monkeypatch.setattr(app.updater, 'progress', lambda: {'state': 'running', 'log': []})
    assert client.post('/update/start').data == b'Update started'
    assert started == [True] and app.main.plot_lock.locked()
    # no plot can be started in the meantime
    assert client.post('/start_plot', data=PLOT).status_code in (400, 409)

    monkeypatch.setattr(app.updater, 'progress', lambda: {'state': 'failed', 'log': []})
    assert wait_for(lambda: not app.main.plot_lock.locked(), timeout=6)


def test_an_update_that_cannot_start_does_not_keep_the_lock(app, client, up, monkeypatch):
    def refuse():
        raise RuntimeError('needs passwordless sudo')

    monkeypatch.setattr(app.updater, 'start', refuse)
    response = client.post('/update/start')
    assert response.status_code == 500 and b'passwordless sudo' in response.data
    assert not app.main.plot_lock.locked()


def test_no_update_while_a_plot_runs(app, client, up, slow_plot, monkeypatch):
    monkeypatch.setattr(app.updater, 'start', lambda: pytest.fail('started during a plot'))
    (app.dir / 'uploads' / 'a.hpgl').write_text('IN;')
    assert client.post('/start_plot', data=PLOT).data == b'Plot started'
    assert wait_for(lambda: app.main.plot_lock.locked())
    assert client.post('/update/start').status_code == 409
    slow_plot['release'] = True
    assert wait_for(lambda: not app.main.plot_lock.locked())


def test_no_update_while_the_queue_runs(app, client, up, monkeypatch):
    monkeypatch.setattr(app.updater, 'start', lambda: pytest.fail('started during the queue'))
    app.globals.queue_active = True
    assert client.post('/update/start').status_code == 409
    assert not app.main.plot_lock.locked()


# ---- running the installer --------------------------------------------------------------------

@pytest.fixture
def systemd(app, up, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return types.SimpleNamespace(returncode=0, stdout='')

    monkeypatch.setattr(app.updater.subprocess, 'run', run)
    monkeypatch.setattr(app.updater, 'source', lambda: ('someone/fork', 'feature/x'))
    return calls


def test_the_installer_runs_in_its_own_unit_with_the_installs_own_source(app, up, systemd):
    app.updater.start()
    [command] = systemd
    assert command[:4] == ['sudo', '-n', 'systemd-run', '--unit=webplotter-update']
    assert '--uid=1000' in command and '--gid=1001' in command and '--collect' in command
    assert '--setenv=WEBPLOTTER_REPO=https://github.com/someone/fork.git' in command
    assert '--setenv=WEBPLOTTER_BRANCH=feature/x' in command
    assert '--setenv=WEBPLOTTER_NO_REBOOT=1' in command
    assert command[-3:-1] == ['/bin/bash', '-c'] and 'WEBPLOTTER_UPDATE_STATUS' in command[-1]
    # the installer was fetched from that same place, and kept for the unit to run
    assert up.asked == ['install.sh']
    assert (up.home / 'webplotter-update.sh').read_bytes() == up.installer


def test_nothing_from_a_request_reaches_the_command(app, client, up, systemd, monkeypatch):
    answers = iter([{'state': 'idle', 'log': []}])      # start() looks once, then the watcher sees it end
    monkeypatch.setattr(app.updater, 'progress', lambda: next(answers, {'state': 'done', 'log': []}))
    client.post('/update/start', data={'repo': 'evil/repo', 'branch': 'evil', 'script': 'rm -rf /'},
                query_string={'repo': 'evil/repo'})
    assert all('evil' not in part and 'rm -rf' not in part for command in systemd for part in command)
    assert wait_for(lambda: not app.main.plot_lock.locked(), timeout=6)


def test_something_that_is_not_the_installer_is_not_run(app, up, systemd):
    up.installer = b'<html>404</html>'
    with pytest.raises(RuntimeError, match='not the installer'):
        app.updater.start()
    assert systemd == []
    up.installer = b'#!/bin/bash\n' + b'#' * (2 * 1024 * 1024)
    with pytest.raises(RuntimeError, match='not the installer'):
        app.updater.start()


def test_a_download_that_fails_says_so(app, up, systemd):
    up.error = 'ConnectionError'
    with pytest.raises(RuntimeError, match='could not be downloaded'):
        app.updater.start()
    assert systemd == []


def test_a_systemd_failure_is_reported(app, up, monkeypatch):
    monkeypatch.setattr(app.updater.subprocess, 'run', lambda *a, **k: types.SimpleNamespace(returncode=1, stdout='boom'))
    with pytest.raises(RuntimeError, match='systemd-run failed'):
        app.updater.start()


def test_it_is_refused_when_it_is_not_allowed(app, up, systemd, monkeypatch):
    monkeypatch.setattr(app.updater, 'can_update', lambda: (False, 'needs passwordless sudo'))
    with pytest.raises(RuntimeError, match='passwordless sudo'):
        app.updater.start()
    assert systemd == [] and up.asked == []


def test_a_second_update_is_refused_while_one_runs(app, up, systemd):
    (up.home / 'webplotter-update.status').write_text('running\n')
    with pytest.raises(RuntimeError, match='already running'):
        app.updater.start()


def test_can_update_explains_what_is_missing(app, monkeypatch, tmp_path):
    updater = app.updater
    monkeypatch.setattr(updater, 'home', lambda: str(tmp_path))
    ok, why = updater.can_update()
    assert ok is False and why        # the test copy is not in ~/webplotter (and this may be Windows)


def test_progress_reads_what_the_unit_leaves(app, up):
    status, log = up.home / 'webplotter-update.status', up.home / 'webplotter-update.log'
    assert app.updater.progress() == {'state': 'idle', 'log': []}
    log.write_text('step 1\n\nstep 2\n')
    for text, state in (('running', 'running'), ('done', 'done'), ('failed 3', 'failed'), ('???', 'idle')):
        status.write_text(text + '\n')
        assert app.updater.progress() == {'state': state, 'log': ['step 1', 'step 2']}
    # an update that has "run" for hours has died
    status.write_text('running\n')
    old = time.time() - app.updater.STALE_S - 10
    os.utime(status, (old, old))
    assert app.updater.progress()['state'] == 'failed'


# ---- the installer script ---------------------------------------------------------------------

def test_the_installer_asks_for_passwordless_sudo():
    with open(os.path.join(ROOT, 'install.sh'), newline='') as f:
        script = f.read()
    assert '\r' not in script
    assert 'sudo -n -k true' in script                      # tested before a typed password is remembered
    assert script.index('check_passwordless_sudo\nensure_sudo\noffer_passwordless_sudo') > 0
    assert 'visudo -cf' in script and '/etc/sudoers.d/' in script       # a broken rule must not reach sudo
    assert 'WEBPLOTTER_SUDO_NOPASSWD' in script
