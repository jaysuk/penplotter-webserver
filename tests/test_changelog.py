"""The changelog: parsing, what is shown after an update, and keeping it in step with VERSION."""
import os

import pytest

from test_notifications import save                       # noqa: F401
from test_updates import up                               # noqa: F401  (a fixture)
from test_routes import PLOT

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SAMPLE = """# Changelog

Some words before the first entry.

## 1.2.0 - 2026-11-01

### Added
- A new thing that is long
  and continues here.
- Another thing

### Fixed
- A bug

## [1.10.0] - 2026-12-01
- Straight under the heading <b>bold</b>

## 1.1.0
### Changed
- Older
"""


def test_the_real_changelog_matches_the_version_file(app):
    with open(os.path.join(ROOT, 'VERSION')) as f:
        version = f.read().strip()
    entries = app.changelog.load(os.path.join(ROOT, 'CHANGELOG.md'))
    assert entries and entries[0]['version'] == version, 'raise VERSION and add its entry to CHANGELOG.md together'
    assert all(group['items'] for entry in entries for group in entry['groups'])
    assert entries[0]['date']


def test_entries_are_parsed_newest_first(app):
    entries = app.changelog.parse(SAMPLE)
    assert [e['version'] for e in entries] == ['1.10.0', '1.2.0', '1.1.0']        # 1.10 is newer than 1.2
    new = entries[1]
    assert new['date'] == '2026-11-01'
    assert [g['title'] for g in new['groups']] == ['Added', 'Fixed']
    assert new['groups'][0]['items'] == ['A new thing that is long and continues here.', 'Another thing']
    assert entries[0]['groups'] == [{'title': '', 'items': ['Straight under the heading <b>bold</b>']}]
    assert entries[2]['date'] == ''


def test_between_is_after_exclusive_and_up_to_inclusive(app):
    entries = app.changelog.parse(SAMPLE)

    def versions(**kwargs):
        return [e['version'] for e in app.changelog.between(entries, **kwargs)]

    assert versions(after='1.1.0') == ['1.10.0', '1.2.0']
    assert versions(after='1.1.0', upto='1.2.0') == ['1.2.0']
    assert versions(after='1.10.0') == []
    assert versions(upto='1.1') == ['1.1.0']


def test_hostile_text_stays_text_and_is_limited(app):
    text = '## 1.0.0\n- ' + 'x' * 5000 + '\n- a\x00b\x1bc\n' + ''.join('- item {}\n'.format(i) for i in range(500))
    text += ''.join('## 0.0.{}\n- x\n'.format(i) for i in range(500))
    entries = app.changelog.parse(text)
    assert len(entries) <= app.changelog.MAX_ENTRIES
    first = [e for e in entries if e['version'] == '1.0.0'][0]
    items = first['groups'][0]['items']
    assert len(items) <= app.changelog.MAX_ITEMS and len(items[0]) <= app.changelog.MAX_TEXT
    assert 'abc' in items and not any('\x00' in i or '\x1b' in i for i in items)
    assert app.changelog.parse('') == [] and app.changelog.parse('no headings at all\n- a\n') == []


def test_a_missing_file_is_an_empty_changelog(app, tmp_path):
    assert app.changelog.load(str(tmp_path / 'nope.md')) == []


# ---- the route --------------------------------------------------------------------------------

@pytest.fixture
def notes(app, monkeypatch):
    entries = app.changelog.parse(SAMPLE)
    monkeypatch.setattr(app.main.changelog, 'load', lambda *a: entries)
    monkeypatch.setattr(app.updater, 'current_version', lambda: '1.2.0')


def versions(response):
    return [e['version'] for e in response.get_json()['entries']]


def test_what_a_browser_has_not_seen_yet(client, notes):
    assert versions(client.get('/changelog?since=1.1.0')) == ['1.2.0']       # not 1.10.0: this install is 1.2.0
    assert versions(client.get('/changelog?since=1.2.0')) == []
    assert versions(client.get('/changelog?since=9.0.0')) == []              # a newer one seen elsewhere
    assert client.get('/changelog?since=1.1.0').get_json()['current'] == '1.2.0'


def test_a_browser_that_has_never_looked_gets_this_version_only(client, notes):
    assert versions(client.get('/changelog?since=none')) == ['1.2.0']


def test_without_a_version_the_latest_entries_are_listed(client, notes):
    assert versions(client.get('/changelog')) == ['1.10.0', '1.2.0', '1.1.0']


@pytest.mark.parametrize('since', ['x', '1.0.0; drop', '1..2', '../../etc'])
def test_a_bad_version_is_refused(client, notes, since):
    assert client.get('/changelog', query_string={'since': since}).status_code == 400


def test_the_page_shows_the_changelog_after_an_update(client):
    page = client.get('/').get_data(as_text=True)
    assert 'id="modal-changelog"' in page and 'maybeShowChangelog()' in page
    with open(os.path.join(ROOT, 'static', 'main.js'), encoding='utf-8') as f:
        js = f.read()
    assert 'webplotter-seen-version' in js and '.text(item)' in js        # the notes are put in as text


# ---- what the update dialog says before updating ----------------------------------------------

def test_the_changes_of_a_new_version_are_known_before_updating(app, up):
    text = '## 1.0.1 - 2026-10-20\n### Fixed\n- Something\n\n## 1.0.0\n- Old\n'
    original = app.updater._get

    def get(path, limit):
        return text.encode() if path == 'CHANGELOG.md' else original(path, limit)

    app.updater._get = get
    app.updater.check()
    info = app.updater.status()
    assert [e['version'] for e in info['changes']] == ['1.0.1']            # not 1.0.0: that is installed already
    assert info['changes'][0]['groups'][0]['items'] == ['Something']


def test_a_missing_remote_changelog_does_not_spoil_the_check(app, up):
    original = app.updater._get

    def get(path, limit):
        if path == 'CHANGELOG.md':
            raise RuntimeError('GitHub answered 404')
        return original(path, limit)

    app.updater._get = get
    app.updater.check()
    info = app.updater.status()
    assert info['available'] is True and info['changes'] == [] and info['error'] is None


# ---- HP-IB is not offered ---------------------------------------------------------------------

def test_hpib_is_not_offered_or_accepted(app, client, uploads):
    page = client.get('/').get_data(as_text=True)
    assert 'HP-IB' not in page
    assert 'HP-IB' not in app.main.FLOW_CONTROLS
    (uploads / 'a.hpgl').write_text('IN;')
    assert client.post('/start_plot', data=dict(PLOT, flowControl='HP-IB')).status_code == 400
    assert client.post('/save_configfile', data={'plotter_flowControl': 'HP-IB'}).status_code == 400
    assert client.post('/plotters', data={'name': 'x', 'flowControl': 'HP-IB'}).status_code == 400


def test_an_old_hpib_setting_shows_the_default(app, client):
    app.main.config.set('plotter', 'flowcontrol', 'HP-IB')
    assert client.get('/save_configfile').get_json()['plotter_flowControl'] == 'CTS/RTS'
