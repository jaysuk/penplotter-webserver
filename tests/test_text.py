"""Typed text as a drawing: the pure part (validation, vpype commands) and the route."""
import importlib.util
import os
import shlex

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FORM = dict(text='Hello world', font='futural', size='20', align='left', outputsize='a4',
            pageorientation='portrait', margin='15')


@pytest.fixture
def td():
    spec = importlib.util.spec_from_file_location('text_drawing_under_test', os.path.join(ROOT, 'text_drawing.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---- text_drawing ---------------------------------------------------------------------------

def test_clean_text(td):
    assert td.clean_text('Hello') == 'Hello'
    assert td.clean_text('\n\nline one  \r\nline two\r\n\n') == 'line one\nline two'
    assert td.clean_text('a\n\nb') == 'a\n\nb'                    # a blank line in the middle is kept


@pytest.mark.parametrize('text', [None, '', '   ', '\n\n', 'x' * 301, '\n'.join('a' * 5 for _ in range(16)),
                                  'bell\x07', 'null\x00', 'tab\x0bvertical', 'del\x7f',
                                  '50%', '%x%', '{x}', 'a{b', 'a}b'])
def test_refused_text(td, text):
    with pytest.raises(td.TextError):
        td.clean_text(text)


def test_limits_are_inclusive(td):
    assert td.clean_text('x' * 300) and td.clean_text('\n'.join('a' for _ in range(15)))


@pytest.mark.parametrize('text', ['quotes "double" \'single\'', '$HOME `id` ; & | < > \\', '-minus', 'accents éü'])
def test_awkward_characters_are_allowed(td, text):
    assert td.clean_text(text) == text.rstrip()


@pytest.mark.parametrize('text,name', [
    ('Hello world', 'text-hello-world.svg'), ('  Hello, World!  ', 'text-hello-world.svg'),
    ('\nsecond line first', 'text-second-line-first.svg'), ('???', 'text-text.svg'),
    ('x' * 100, 'text-' + 'x' * 30 + '.svg'), ('../../etc/passwd', 'text-etc-passwd.svg'),
])
def test_file_names(td, text, name):
    assert td.file_name(td.clean_text(text) if text.strip() else text) == name


def test_commands_for_one_line(td):
    assert td.commands('Hello', 'futural', 20, 'a4', False, 15, 'left') == (
        "text -f futural -s 20mm -a left -p 0 0mm -- Hello "
        "layout --align left --valign top a4 translate -- 15mm 15mm")


def test_commands_for_several_lines(td):
    result = td.commands('one\n\ntwo', 'timesr', 10, 'a3', True, 20, 'right')
    # every line is placed 1.4 sizes below the one before; blank lines leave a gap
    assert result.count(' text ') + result.startswith('text ') == 2
    assert '-p 0 0mm -- one' in result and '-p 0 28mm -- two' in result
    assert result.endswith('layout -l --align right --valign top a3 translate -- -20mm 20mm')


def test_text_is_quoted_for_the_command_line(td):
    nasty = 'say "hi" $(id) `x` ; rm -rf / \'q\' \\ -minus'
    result = td.commands(nasty, 'futural', 20, 'a4', False, 15, 'left')
    words = shlex.split(result)
    assert words[words.index('--') + 1] == nasty               # it comes back out as one word


@pytest.mark.parametrize('font,align,page', [('comic', 'left', 'a4'), ('futural', 'justify', 'a4'),
                                              ('futural', 'left', 'a9')])
def test_unknown_options_are_refused(td, font, align, page):
    with pytest.raises(td.TextError):
        td.commands('Hello', font, 20, page, False, 15, align)


def test_empty_text_has_no_commands(td):
    with pytest.raises(td.TextError):
        td.commands('\n\n', 'futural', 20, 'a4', False, 15, 'left')


td_mm_per_px = 25.4 / 96


@pytest.mark.parametrize('bounds_mm,ok', [
    ((15, 15, 195, 100), True), ((14.6, 15, 195, 100), True), ((14.4, 15, 195, 100), False),
    ((15, 15, 195.4, 100), True), ((15, 15, 195.6, 100), False), ((15, 15, 100, 282.4), True),
    ((15, 15, 100, 282.6), False),
])
def test_fit_check(td, bounds_mm, ok):
    bounds = tuple(v / td_mm_per_px for v in bounds_mm)
    if ok:
        td.check_fit(bounds, 'a4', False, 15)
    else:
        with pytest.raises(td.TextError):
            td.check_fit(bounds, 'a4', False, 15)


def test_fit_check_uses_the_orientation(td):
    wide = tuple(v / td_mm_per_px for v in (15, 15, 280, 100))        # fits A4 landscape, not portrait
    td.check_fit(wide, 'a4', True, 15)
    with pytest.raises(td.TextError):
        td.check_fit(wide, 'a4', False, 15)


# ---- the route ------------------------------------------------------------------------------

def test_create_text(app, client, uploads):
    response = client.post('/create_text', data=FORM)
    assert response.status_code == 200 and response.data == b'Created text-hello-world.svg'
    assert (uploads / 'text-hello-world.svg').exists()
    [(args, kwargs)] = app.convert_stub.text_calls
    assert args == ('Hello world', 'futural', 20.0, 'a4', False, 15.0, 'left')
    assert kwargs == {'output': 'uploads/text-hello-world.svg'}


def test_create_text_options(app, client):
    client.post('/create_text', data={**FORM, 'text': 'A\nB', 'font': 'cursive', 'size': '7.5', 'align': 'center',
                                      'outputsize': 'a3', 'pageorientation': 'landscape', 'margin': '0'})
    [(args, _)] = app.convert_stub.text_calls
    assert args == ('A\nB', 'cursive', 7.5, 'a3', True, 0.0, 'center')


def test_margin_defaults_to_15(app, client):
    client.post('/create_text', data={k: v for k, v in FORM.items() if k != 'margin'})
    assert app.convert_stub.text_calls[0][0][5] == 15.0


@pytest.mark.parametrize('field,value', [
    ('text', ''), ('text', '50%'), ('text', '{x}'), ('text', 'x' * 301),
    ('font', 'comic'), ('font', ''), ('align', 'justify'), ('outputsize', 'a9'), ('pageorientation', 'tilted'),
    ('size', ''), ('size', '2.9'), ('size', '201'), ('size', 'x'), ('size', '1e1'), ('size', '10\n'), ('size', '١٢'),
    ('margin', '51'), ('margin', '-1'), ('margin', 'x'),
])
def test_bad_options_are_refused(app, client, uploads, field, value):
    assert client.post('/create_text', data={**FORM, field: value}).status_code == 400
    assert app.convert_stub.text_calls == [] and os.listdir(uploads) == []


def test_text_that_does_not_fit_says_so(app, client, uploads):
    app.convert_stub.text_error = app.main.text_drawing.TextError('The text does not fit on the page')
    response = client.post('/create_text', data=FORM)
    assert response.status_code == 400 and b'does not fit' in response.data


def test_a_converter_crash_is_reported(app, client, monkeypatch):
    monkeypatch.setattr(app.main, 'make_text_svg', lambda *a, **k: 1 / 0)
    response = client.post('/create_text', data=FORM)
    assert response.status_code == 500 and b'Could not create the text' in response.data


def test_the_name_cannot_leave_uploads(app, client, uploads):
    client.post('/create_text', data={**FORM, 'text': '../../etc/passwd'})
    assert app.convert_stub.text_calls[0][1]['output'] == 'uploads/text-etc-passwd.svg'
    assert os.listdir(uploads) == ['text-etc-passwd.svg']


def test_create_text_is_post_only(client):
    assert client.get('/create_text').status_code == 405
