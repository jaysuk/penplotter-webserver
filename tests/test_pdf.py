"""Importing a PDF as an svg. poppler is not needed: pdftocairo is faked."""
import io
import os
import subprocess

import pytest

PDF = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n'
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="210mm" height="297mm"></svg>'


@pytest.fixture
def poppler(app, monkeypatch):
    """A working pdftocairo that writes SVG to the output path. Returns what it was called with."""
    calls = []

    def which(name):
        return '/usr/bin/pdftocairo' if name == 'pdftocairo' else None

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert os.path.isfile(command[-2])                   # the pdf exists while it runs
        if run.behaviour == 'timeout':
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        if run.behaviour == 'oserror':
            raise OSError('cannot execute')
        if run.behaviour == 'ok':
            with open(command[-1], 'wb') as f:
                f.write(SVG)
        if run.behaviour == 'empty':
            open(command[-1], 'wb').close()
        return subprocess.CompletedProcess(command, 1 if run.behaviour == 'fail' else 0, stderr=b'bad pdf')

    run.behaviour = 'ok'
    monkeypatch.setattr(app.main.shutil, 'which', which)
    monkeypatch.setattr(app.main.subprocess, 'run', run)
    app.pdf_run = run
    return calls


def upload(client, name='drawing.pdf', content=PDF):
    return client.post('/', data={'file': (io.BytesIO(content), name)}, content_type='multipart/form-data')


def test_a_pdf_becomes_an_svg(app, client, uploads, poppler):
    response = upload(client)
    assert response.status_code == 204
    assert (uploads / 'drawing.svg').read_bytes() == SVG
    assert os.listdir(uploads) == ['drawing.svg']             # the pdf itself is not kept
    assert not any(os.scandir(app.dir / 'cache' / 'pdf'))      # nor anything in the work folder
    [(command, kwargs)] = poppler
    assert command[:6] == ['/usr/bin/pdftocairo', '-svg', '-f', '1', '-l', '1']
    assert command[6:] == [os.path.join('cache', 'pdf', 'drawing.pdf'), os.path.join('cache', 'pdf', 'drawing.svg')]
    assert kwargs['timeout'] == app.main.PDF_TIMEOUT and kwargs['stdin'] == subprocess.DEVNULL


def test_the_svg_can_be_converted_like_any_other(app, client, uploads, poppler):
    upload(client)
    files = [f['name'] for f in client.get('/update_files').get_json()['content']]
    assert files == ['drawing.svg']


def test_uppercase_extension_and_odd_names(app, client, uploads, poppler):
    assert upload(client, 'My Drawing (1).PDF').status_code == 204
    assert os.listdir(uploads) == ['My_Drawing_1.svg']
    assert upload(client, '../../evil.pdf').status_code == 204
    assert sorted(os.listdir(uploads)) == ['My_Drawing_1.svg', 'evil.svg']


def test_the_import_is_logged(app, client, uploads, poppler):
    watcher = app.main.socketio.test_client(app.main.app)
    watcher.get_received()
    upload(client)
    logs = [m['args'][0]['data'] for m in watcher.get_received() if m['name'] == 'status_log']
    assert any('Imported page 1 of drawing.pdf' in line for line in logs)


@pytest.mark.parametrize('behaviour,message', [
    ('fail', b'Could not read the PDF'), ('empty', b'Could not read the PDF'), ('nothing', b'Could not read the PDF'),
    ('timeout', b'took too long'), ('oserror', b'Could not run pdftocairo'),
])
def test_failures_are_reported_and_cleaned_up(app, client, uploads, poppler, behaviour, message):
    app.pdf_run.behaviour = behaviour
    response = upload(client)
    assert response.status_code == 400 and message in response.data
    assert os.listdir(uploads) == []
    assert not any(os.scandir(app.dir / 'cache' / 'pdf'))


@pytest.mark.parametrize('content', [b'', b'not a pdf', b'GIF89a', b'%PD'])
def test_only_real_pdfs_are_given_to_poppler(app, client, uploads, poppler, content):
    response = upload(client, content=content)
    assert response.status_code == 400 and b'not a PDF' in response.data
    assert poppler == []


def test_without_poppler_pdfs_are_refused_with_the_fix(app, client, uploads, monkeypatch):
    monkeypatch.setattr(app.main.shutil, 'which', lambda name: None)
    response = upload(client)
    assert response.status_code == 400 and b'sudo apt install poppler-utils' in response.data
    assert os.listdir(uploads) == []


def test_a_pdf_replaces_an_svg_of_the_same_name(app, client, uploads, poppler):
    (uploads / 'drawing.svg').write_text('old')
    assert upload(client).status_code == 204                   # like uploading an svg with that name
    assert (uploads / 'drawing.svg').read_bytes() == SVG


def test_the_page_offers_pdfs_only_when_they_can_be_read(app, client, monkeypatch):
    monkeypatch.setattr(app.main.shutil, 'which', lambda name: None)
    assert ".pdf" not in client.get('/').get_data(as_text=True).split('acceptedFiles')[1][:40]
    monkeypatch.setattr(app.main.shutil, 'which', lambda name: '/usr/bin/pdftocairo')
    assert '.svg,.hpgl,.cal,.pdf' in client.get('/').get_data(as_text=True)


def test_other_extensions_are_still_refused(client, uploads, poppler):
    assert upload(client, 'x.exe', b'MZ').status_code == 400
    assert upload(client, '.pdf').status_code == 400
