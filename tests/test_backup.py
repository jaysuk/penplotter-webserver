"""Backing up and restoring settings, history and files."""
import io
import json
import os
import sqlite3
import zipfile

import pytest

from test_routes import PLOT, slow_plot, wait_for, wait_until_plotting      # noqa: F401  (slow_plot is a fixture)

MANIFEST = json.dumps({'app': 'webplotter', 'format': 1, 'created': 1, 'contains': []})


def download(client, uploads=False):
    response = client.get('/backup' + ('?uploads=1' if uploads else ''))
    assert response.status_code == 200
    return zipfile.ZipFile(io.BytesIO(response.data)), response


def make_zip(members, manifest=MANIFEST):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        if manifest is not None:
            archive.writestr('manifest.json', manifest)
        for name, data in members.items():
            archive.writestr(name, data)
    buffer.seek(0)
    return buffer


def restore(client, archive, name='backup.zip'):
    return client.post('/restore', data={'backup': (archive, name)}, content_type='multipart/form-data')


def history_db(rows=(), extra_sql=''):
    """The bytes of a web plotter history with the given (file, status) rows."""
    path = 'tmp-restore-source.db'
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, file TEXT NOT NULL, port TEXT, baudrate INTEGER, '
                 'flow_control TEXT, started_at REAL NOT NULL, finished_at REAL, status TEXT NOT NULL, progress INTEGER DEFAULT 0, error TEXT)')
    for file, status in rows:
        conn.execute('INSERT INTO jobs (file, started_at, status) VALUES (?, 1, ?)', (file, status))
    if extra_sql:
        conn.executescript(extra_sql)
    conn.commit()
    conn.close()
    with open(path, 'rb') as f:
        data = f.read()
    os.remove(path)
    return data


# ---- making a backup --------------------------------------------------------------------------

def test_a_backup_holds_the_settings_the_history_and_a_manifest(app, client):
    client.post('/save_configfile', data={'plotter_name': 'Backup Plotter'})
    app.history.finish(app.history.start('a.hpgl', '/dev/x', 9600, 'None'), 'completed', 100)

    archive, response = download(client)
    assert sorted(archive.namelist()) == ['config.ini', 'history.db', 'manifest.json']
    assert 'attachment' in response.headers['Content-Disposition'] and '.zip' in response.headers['Content-Disposition']
    assert b'Backup Plotter' in archive.read('config.ini')
    assert json.loads(archive.read('manifest.json'))['format'] == 1
    with open('backup-check.db', 'wb') as f:                 # Connection.deserialize needs Python 3.11
        f.write(archive.read('history.db'))
    copy = sqlite3.connect('backup-check.db')
    try:
        assert copy.execute('SELECT file FROM jobs').fetchall() == [('a.hpgl',)]
    finally:
        copy.close()
        os.remove('backup-check.db')


def test_uploads_are_only_included_when_asked_for(client, uploads):
    (uploads / 'a.hpgl').write_bytes(b'IN;')
    (uploads / 'b.svg').write_bytes(b'<svg/>')
    (uploads / 'notes.txt').write_bytes(b'x')
    assert 'uploads/a.hpgl' not in download(client)[0].namelist()
    names = download(client, uploads=True)[0].namelist()
    assert 'uploads/a.hpgl' in names and 'uploads/b.svg' in names and 'uploads/notes.txt' not in names


def test_the_temporary_file_is_removed(client, tmp_path):
    import tempfile
    before = set(os.listdir(tempfile.gettempdir()))
    client.get('/backup', buffered=True)            # reading it all closes the response, which removes the file
    leftovers = [n for n in set(os.listdir(tempfile.gettempdir())) - before if n.endswith('.zip') or n.endswith('.db')]
    assert leftovers == []


def test_a_backup_can_be_made_while_plotting(app, client, uploads, slow_plot):
    (uploads / 'a.hpgl').write_bytes(b'IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_until_plotting(app)
    assert client.get('/backup').status_code == 200
    client.post('/stop_plot')


# ---- restoring --------------------------------------------------------------------------------

def test_a_backup_restores_into_a_changed_setup(app, client, uploads):
    client.post('/save_configfile', data={'plotter_name': 'Original', 'notify_progress_every': '10'})
    app.history.finish(app.history.start('kept.hpgl', '/dev/x', 9600, 'None'), 'completed', 100)
    (uploads / 'a.hpgl').write_bytes(b'IN;SP1;')
    app.presets.save('mine', {'outputsize': 'a3'}) if hasattr(app.presets, 'save') else None
    data = client.get('/backup?uploads=1').data

    client.post('/save_configfile', data={'plotter_name': 'Changed', 'notify_progress_every': '0'})
    app.history.clear()
    (uploads / 'a.hpgl').write_bytes(b'something else')
    (uploads / 'other.hpgl').write_bytes(b'IN;')

    response = restore(client, io.BytesIO(data))
    assert response.status_code == 200
    assert response.get_json()['history'] is True and response.get_json()['uploads'] == 1
    shown = client.get('/save_configfile').get_json()
    assert shown['plotter_name'] == 'Original' and shown['notify_progress_every'] == '10'
    assert [j['file'] for j in client.get('/job_history').get_json()] == ['kept.hpgl']
    assert (uploads / 'a.hpgl').read_bytes() == b'IN;SP1;'
    assert (uploads / 'other.hpgl').exists()                         # other files stay


def test_a_restored_config_is_written_to_disk(app, client):
    data = make_zip({'config.ini': '[plotter]\nname = From A Backup\n'})
    assert restore(client, data).get_json()['config'] == 1
    assert 'From A Backup' in open('config.ini').read()
    assert client.get('/save_configfile').get_json()['plotter_name'] == 'From A Backup'


def test_percent_signs_survive_a_restore(app, client):
    client.post('/save_configfile', data={'plotter_name': '100% plotter'})
    data = client.get('/backup').data
    client.post('/save_configfile', data={'plotter_name': 'x'})
    assert restore(client, io.BytesIO(data)).status_code == 200
    assert client.get('/save_configfile').get_json()['plotter_name'] == '100% plotter'


def test_only_known_settings_are_restored(app, client):
    data = make_zip({'config.ini': '[plotter]\nname = Ok\nsomething = else\n[evil]\nkey = value\n'})
    assert restore(client, data).get_json()['config'] == 1
    assert not app.main.config.has_section('evil') and not app.main.config.has_option('plotter', 'something')


def test_invalid_settings_stop_the_restore_before_anything_changes(app, client, uploads):
    client.post('/save_configfile', data={'plotter_name': 'Before'})
    data = make_zip({'config.ini': '[plotter]\nname = Fine\nbaudrate = fast\n', 'history.db': history_db([('x.hpgl', 'completed')]),
                     'uploads/new.hpgl': 'IN;'})
    response = restore(client, data)
    assert response.status_code == 400 and b'baudrate' in response.data
    assert client.get('/save_configfile').get_json()['plotter_name'] == 'Before'
    assert client.get('/job_history').get_json() == [] and not (uploads / 'new.hpgl').exists()


def test_a_login_is_restored_only_when_it_is_complete(app, client):
    assert restore(client, make_zip({'config.ini': '[auth]\nusername = admin\n'})).get_json()['config'] == 0
    assert not app.main.auth_configured()
    assert restore(client, make_zip({'config.ini': '[auth]\nusername = admin\npassword = pw\n'})).get_json()['config'] == 2
    assert app.main.auth_configured()


def test_an_empty_password_in_a_backup_keeps_the_current_one(app, client):
    client.post('/save_configfile', data={'mqtt_host': 'broker', 'mqtt_password': 'keepme'})
    restore(client, make_zip({'config.ini': '[notifications]\nmqtt_host = other\nmqtt_password =\n'}))
    settings = app.main.notification.mqtt_settings()
    assert settings['host'] == 'other' and settings['password'] == 'keepme'


def test_an_older_history_gets_the_new_columns(app, client):
    restore(client, make_zip({'history.db': history_db([('old.hpgl', 'completed')])}))
    [job] = client.get('/job_history').get_json()
    assert job['file'] == 'old.hpgl' and job['estimate_s'] is None and job['can_replot'] is False
    app.history.finish(app.history.start('new.hpgl', '/dev/x', 9600, 'None', {'file': 'new.hpgl'}), 'completed')   # new columns work


def test_a_plot_that_was_running_in_the_backup_is_interrupted(app, client):
    restore(client, make_zip({'history.db': history_db([('x.hpgl', 'running')])}))
    assert client.get('/job_history').get_json()[0]['status'] == 'interrupted'


def test_the_queue_and_presets_come_back(app, client, uploads):
    (uploads / 'a.hpgl').write_bytes(b'IN;')
    client.post('/queue/add', data=dict(PLOT, file='a.hpgl'))
    data = client.get('/backup').data
    client.post('/queue/clear')
    restore(client, io.BytesIO(data))
    assert [i['file'] for i in client.get('/queue').get_json()['items']] == ['a.hpgl']


# ---- bad archives -----------------------------------------------------------------------------

@pytest.mark.parametrize('name', ['../evil.hpgl', 'uploads/../evil.hpgl', '/etc/passwd', 'uploads/sub/x.hpgl',
                                  'uploads/x.exe', 'uploads/.hpgl', 'other.txt', 'uploads/', 'uploads/a b.hpgl'])
def test_members_outside_the_backup_format_are_refused(app, client, uploads, name):
    response = restore(client, make_zip({name: 'x', 'config.ini': '[plotter]\nname = Changed\n'}))
    assert response.status_code == 400
    assert client.get('/save_configfile').get_json()['plotter_name'] != 'Changed'      # nothing was applied
    assert not (uploads.parent / 'evil.hpgl').exists()


def test_the_member_name_pattern_is_strict(app):
    pattern = app.main.backup.UPLOAD_NAME_RE
    for bad in ('uploads\\x.hpgl', 'uploads/x.hpgl\n', 'uploads/../x.hpgl', 'uploads/x.hpgl/', ' uploads/x.hpgl'):
        assert pattern.fullmatch(bad) is None, bad
    assert pattern.fullmatch('uploads/ok-name_1.2.HPGL')


def test_a_zip_without_a_manifest_or_with_another_format_is_refused(client):
    assert restore(client, make_zip({'config.ini': '[plotter]\nname = x\n'}, manifest=None)).status_code == 400
    assert restore(client, make_zip({}, manifest='not json')).status_code == 400
    assert restore(client, make_zip({}, manifest=json.dumps({'app': 'webplotter', 'format': 99}))).status_code == 400
    assert restore(client, make_zip({}, manifest=json.dumps({'app': 'other', 'format': 1}))).status_code == 400
    assert restore(client, make_zip({}, manifest='[]')).status_code == 400


def test_things_that_are_not_zips_are_refused(client):
    assert restore(client, io.BytesIO(b'definitely not a zip')).status_code == 400
    assert client.post('/restore').status_code == 400
    assert client.get('/restore').status_code == 405


def test_a_damaged_or_foreign_history_is_refused(client):
    assert restore(client, make_zip({'history.db': b'not a database' * 100})).status_code == 400
    other = history_db()
    conn = sqlite3.connect('foreign.db')
    conn.execute('CREATE TABLE stuff (a)')
    conn.commit()
    conn.close()
    foreign = open('foreign.db', 'rb').read()
    os.remove('foreign.db')
    assert restore(client, make_zip({'history.db': foreign})).status_code == 400
    assert other  # (the proper one is accepted elsewhere)


def test_a_history_with_triggers_is_refused(app, client):
    evil = history_db([('x.hpgl', 'completed')], "CREATE TRIGGER t AFTER INSERT ON jobs BEGIN DELETE FROM jobs; END;")
    response = restore(client, make_zip({'history.db': evil}))
    assert response.status_code == 400
    assert client.get('/job_history').get_json() == []


def test_oversized_members_are_refused(app, client, monkeypatch):
    monkeypatch.setattr(app.main.backup, 'MAX_MEMBER_BYTES', 100)
    assert restore(client, make_zip({'uploads/big.hpgl': 'x' * 500})).status_code == 400
    monkeypatch.setattr(app.main.backup, 'MAX_MEMBER_BYTES', 10000)
    monkeypatch.setattr(app.main.backup, 'MAX_TOTAL_BYTES', 600)
    assert restore(client, make_zip({'uploads/a.hpgl': 'x' * 400, 'uploads/b.hpgl': 'x' * 400})).status_code == 400
    monkeypatch.setattr(app.main.backup, 'MAX_MEMBERS', 2)
    assert restore(client, make_zip({'uploads/a.hpgl': 'x', 'uploads/b.hpgl': 'x'})).status_code == 400


def test_a_zip_that_lies_about_its_size_is_cut_short(app, client, monkeypatch):
    """The declared size is what the reader honours, so a member cannot grow past it."""
    archive = make_zip({'config.ini': '[plotter]\nname = ' + 'x' * 5000 + '\n'})
    monkeypatch.setattr(app.main.backup, 'MAX_CONFIG_BYTES', 1000)
    assert restore(client, archive).status_code == 400


def test_nothing_is_restored_while_plotting(app, client, uploads, slow_plot):
    (uploads / 'a.hpgl').write_bytes(b'IN;')
    client.post('/start_plot', data=PLOT)
    assert wait_until_plotting(app)
    assert restore(client, make_zip({'config.ini': '[plotter]\nname = Nope\n'})).status_code == 409
    assert client.get('/save_configfile').get_json()['plotter_name'] != 'Nope'
    client.post('/stop_plot')
    assert wait_for(lambda: not app.main.plot_lock.locked())
    assert restore(client, make_zip({'config.ini': '[plotter]\nname = Now\n'})).status_code == 200    # the lock is released


def test_a_restore_that_fails_leaves_no_files_behind(app, client):
    restore(client, make_zip({'history.db': b'broken'}))
    assert not os.path.exists('restore.db')
    assert not app.main.plot_lock.locked()


def test_a_history_from_before_schema_versions_is_migrated(app, client):
    restore(client, make_zip({'history.db': history_db([('old.hpgl', 'completed')])}))      # user_version 0
    with app.history._connect() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == app.history.SCHEMA_VERSION
    assert client.get('/pen_usage').get_json() == {'pens': []}          # and the pen log table exists again


def test_a_history_from_a_newer_version_is_refused(app, client):
    newer = history_db([('x.hpgl', 'completed')], 'PRAGMA user_version = {};'.format(app.history.SCHEMA_VERSION + 1))
    response = restore(client, make_zip({'history.db': newer, 'config.ini': '[plotter]\nname = Changed\n'}))
    assert response.status_code == 400 and b'newer web plotter' in response.data
    assert client.get('/save_configfile').get_json()['plotter_name'] != 'Changed'      # nothing was applied
