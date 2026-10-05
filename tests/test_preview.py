"""Looking at a conversion before it is saved."""
import os
import time

import pytest

from test_routes import CONVERT, PLOT


@pytest.fixture
def svg(uploads):
    (uploads / 'a.svg').write_text('<svg/>')


def preview_dir(app):
    return app.dir / 'cache' / 'preview'


def test_preview_converts_into_the_preview_folder(app, client, svg, uploads):
    response = client.post('/preview_conversion', data=CONVERT)
    assert response.status_code == 200
    data = response.get_json()
    assert data['name'] == 'a-converted.hpgl'
    assert (preview_dir(app) / 'a-converted.hpgl').read_bytes() == app.convert_stub.hpgl
    assert not (uploads / 'a-converted.hpgl').exists()                 # nothing in the file list yet
    [(args, kwargs)] = app.convert_stub.calls
    assert args[0] == 'uploads/a.svg' and kwargs['output'] == os.path.join('cache', 'preview', 'a-converted.hpgl')


def test_preview_says_what_the_plot_will_be_like(app, client, svg):
    summary = client.post('/preview_conversion', data=CONVERT).get_json()['summary']
    assert summary['seconds'] > 0 and summary['travel_mm'] > 0 and summary['draw_mm'] > 0
    assert [p['pen'] for p in summary['pens']] == [1, 2]
    assert summary['width_mm'] == pytest.approx(2400 / app.hpgl.UNITS_PER_MM, rel=0.01)


def test_preview_uses_the_same_validation_as_conversion(app, client, svg):
    assert client.post('/preview_conversion', data={**CONVERT, 'margin': '99'}).status_code == 400
    assert client.post('/preview_conversion', data={**CONVERT, 'command_input': 'eval x'}).status_code == 400
    assert client.post('/preview_conversion', data={**CONVERT, 'file': '../a.svg'}).status_code == 400
    assert client.post('/preview_conversion', data={**CONVERT, 'file': 'missing.svg'}).status_code == 400
    assert app.convert_stub.calls == []


def test_a_failed_preview_is_reported(app, client, svg):
    app.convert_stub.fail = True
    response = client.post('/preview_conversion', data=CONVERT)
    assert response.status_code == 422 and response.data == b'File not converted.'


def test_a_crashing_converter_is_reported(app, client, svg, monkeypatch):
    def explode(*args, **kwargs):
        raise SystemExit(1)            # vpype can exit instead of raising

    monkeypatch.setattr(app.main, 'convert_file', explode)
    assert client.post('/preview_conversion', data=CONVERT).status_code == 422


def test_preview_replaces_an_earlier_one(app, client, svg):
    client.post('/preview_conversion', data=CONVERT)
    app.convert_stub.fail = True       # a failure must not leave the previous result to be saved
    assert client.post('/preview_conversion', data=CONVERT).status_code == 422
    assert not (preview_dir(app) / 'a-converted.hpgl').exists()


def test_the_preview_file_is_served(app, client, svg):
    client.post('/preview_conversion', data=CONVERT)
    response = client.get('/preview_files/a-converted.hpgl')
    assert response.status_code == 200 and response.data == app.convert_stub.hpgl
    assert response.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('name', ['missing.hpgl', '../uploads/a.svg', '..%2Fa.hpgl', 'a.svg', 'a.hpgl.txt', '.hpgl'])
def test_only_previews_are_served(app, client, svg, uploads, name):
    (uploads / 'a.hpgl').write_text('IN;')
    client.post('/preview_conversion', data=CONVERT)
    assert client.get('/preview_files/' + name).status_code == 404


def test_save_moves_the_preview_into_uploads(app, client, svg, uploads):
    client.post('/preview_conversion', data=CONVERT)
    response = client.post('/save_preview', data={'name': 'a-converted.hpgl'})
    assert response.status_code == 200 and response.data == b'Exported uploads/a-converted.hpgl'
    assert (uploads / 'a-converted.hpgl').read_bytes() == app.convert_stub.hpgl
    assert not (preview_dir(app) / 'a-converted.hpgl').exists()
    assert 'a-converted.hpgl' in [f['name'] for f in client.get('/update_files').get_json()['content']]


def test_save_replaces_a_file_with_the_same_name(app, client, svg, uploads):
    (uploads / 'a-converted.hpgl').write_text('old')
    client.post('/preview_conversion', data=CONVERT)
    assert client.post('/save_preview', data={'name': 'a-converted.hpgl'}).status_code == 200
    assert (uploads / 'a-converted.hpgl').read_bytes() == app.convert_stub.hpgl


@pytest.mark.parametrize('name', [None, '', 'missing.hpgl', '../a.hpgl', '/etc/passwd', 'a-converted.hpgl\n',
                                  'a-converted.svg', 'sub/a-converted.hpgl'])
def test_save_only_takes_previews(app, client, svg, uploads, name):
    client.post('/preview_conversion', data=CONVERT)
    data = {} if name is None else {'name': name}
    assert client.post('/save_preview', data=data).status_code == 404
    assert (preview_dir(app) / 'a-converted.hpgl').exists()            # the real one is untouched


def test_save_is_post_only(client):
    assert client.get('/save_preview').status_code == 405
    assert client.get('/preview_conversion').status_code == 405


def test_save_will_not_replace_the_file_being_plotted(app, client, svg, uploads):
    client.post('/preview_conversion', data=CONVERT)
    (uploads / 'a-converted.hpgl').write_text('plotting')
    app.main.plot_lock.acquire()
    app.main.current_plot = 'a-converted.hpgl'
    try:
        assert client.post('/save_preview', data={'name': 'a-converted.hpgl'}).status_code == 409
        assert (uploads / 'a-converted.hpgl').read_text() == 'plotting'
    finally:
        app.main.current_plot = None
        app.main.plot_lock.release()


def test_old_previews_are_cleaned_up(app, client, svg):
    folder = preview_dir(app)
    folder.mkdir(parents=True, exist_ok=True)
    old, recent = folder / 'old.hpgl', folder / 'recent.hpgl'
    old.write_text('IN;')
    recent.write_text('IN;')
    two_days_ago = time.time() - 2 * 24 * 3600
    os.utime(old, (two_days_ago, two_days_ago))
    client.post('/preview_conversion', data=CONVERT)
    assert not old.exists() and recent.exists()


def test_save_that_cannot_move_the_file_says_so(app, client, svg, monkeypatch):
    client.post('/preview_conversion', data=CONVERT)

    def refuse(*args):
        raise PermissionError('in use')

    monkeypatch.setattr(app.main.os, 'replace', refuse)
    response = client.post('/save_preview', data={'name': 'a-converted.hpgl'})
    assert response.status_code == 500 and b'Could not save the file' in response.data
    assert (preview_dir(app) / 'a-converted.hpgl').exists()     # it can be tried again
