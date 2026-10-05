"""Disk space, file ages and cleaning up."""
import os
import shutil
import time
from collections import namedtuple

import pytest

from test_routes import PLOT, slow_plot, wait_for      # noqa: F401  (slow_plot is a fixture)

DAY = 86400


def make(uploads, name, days_old=0, content=b'IN;'):
    path = uploads / name
    path.write_bytes(content)
    stamp = time.time() - days_old * DAY
    os.utime(path, (stamp, stamp))
    return path


def listed(client):
    return {f['name']: f for f in client.get('/update_files').get_json()['content']}


def test_the_file_list_has_sizes_and_times(client, uploads):
    make(uploads, 'a.hpgl', days_old=3, content=b'x' * 1500)
    entry = listed(client)['a.hpgl']
    assert entry['size'] == 1500
    assert abs(time.time() - entry['mtime'] - 3 * DAY) < 60


def test_storage_reports_the_disk_and_what_the_app_uses(app, client, uploads, monkeypatch):
    Usage = namedtuple('Usage', 'total used free')
    monkeypatch.setattr(shutil, 'disk_usage', lambda path: Usage(1000, 400, 600))
    make(uploads, 'a.hpgl', content=b'x' * 100)
    make(uploads, 'b.svg', content=b'y' * 50)
    os.makedirs('cache/analysis', exist_ok=True)
    with open('cache/analysis/x.json', 'wb') as f:
        f.write(b'z' * 30)

    data = client.get('/storage').get_json()
    assert (data['total'], data['used'], data['free']) == (1000, 400, 600)
    assert data['uploads'] == 150 and data['cache'] >= 30
    assert data['history'] > 0                                # history.db exists


def test_storage_survives_a_disk_it_cannot_read(client, monkeypatch):
    def broken(path):
        raise OSError('no')
    monkeypatch.setattr(shutil, 'disk_usage', broken)
    data = client.get('/storage').get_json()
    assert data['total'] is None and data['uploads'] == 0


# ---- delete older than ------------------------------------------------------------------------

def test_old_files_are_deleted_and_new_ones_kept(client, uploads):
    make(uploads, 'old.hpgl', days_old=40)
    make(uploads, 'older.svg', days_old=400)
    make(uploads, 'new.hpgl', days_old=5)
    response = client.post('/delete_old_files', data={'days': '30'})
    assert sorted(response.get_json()) == ['old.hpgl', 'older.svg']
    assert list(listed(client)) == ['new.hpgl']
    assert client.post('/delete_old_files', data={'days': '30'}).get_json() == []


def test_the_age_limit_is_checked(client, uploads):
    make(uploads, 'old.hpgl', days_old=40)
    for days in ('', '0', '-1', 'x', '10000', '3.5', '30\n'):
        assert client.post('/delete_old_files', data={'days': days}).status_code == 400, days
    assert client.get('/delete_old_files').status_code == 405
    assert 'old.hpgl' in listed(client)


def test_files_in_the_queue_are_not_old(app, client, uploads):
    make(uploads, 'queued.hpgl', days_old=400)
    make(uploads, 'gone.hpgl', days_old=400)
    assert client.post('/queue/add', data=dict(PLOT, file='queued.hpgl')).status_code == 200
    assert client.post('/delete_old_files', data={'days': '30'}).get_json() == ['gone.hpgl']
    assert 'queued.hpgl' in listed(client)


def test_nothing_is_deleted_while_plotting(app, client, uploads, slow_plot):
    make(uploads, 'a.hpgl', days_old=400)
    client.post('/start_plot', data=PLOT)
    assert wait_for(lambda: app.main.plot_lock.locked())
    assert client.post('/delete_old_files', data={'days': '30'}).status_code == 409
    assert client.post('/clear_cache').status_code == 409
    assert 'a.hpgl' in listed(client)
    client.post('/stop_plot')


def test_a_plot_can_start_again_after_cleaning_up(app, client, uploads):
    make(uploads, 'a.hpgl', days_old=400)
    client.post('/delete_old_files', data={'days': '30'})
    assert not app.main.plot_lock.locked()                    # the lock taken for the clean up is released


def test_the_cache_is_cleared(client):
    os.makedirs('cache/preview', exist_ok=True)
    with open('cache/preview/p.hpgl', 'wb') as f:
        f.write(b'x' * 200)
    freed = client.post('/clear_cache').get_json()['freed']
    assert freed >= 200 and not os.path.exists('cache')
    assert client.post('/clear_cache').get_json() == {'freed': 0}      # nothing there is fine
    assert client.get('/clear_cache').status_code == 405
