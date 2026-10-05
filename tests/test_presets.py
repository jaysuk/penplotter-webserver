"""Saved sets of conversion options."""
import pytest

from test_routes import CONVERT

FORM = {k: v for k, v in CONVERT.items() if k != 'file'}


def save(client, name='Poster', **extra):
    return client.post('/presets', data={**FORM, 'name': name, **extra})


def test_no_presets_to_begin_with(client):
    assert client.get('/presets').get_json() == []


def test_save_and_list(client):
    response = save(client, 'A3 poster', outputsize='a3', pageorientation='landscape', margin='10', rotate='90',
                    mirror_x='on', linesort='on', linemerge='on', speed='20')
    assert response.status_code == 200 and response.data == b'Saved preset A3 poster'
    [preset] = client.get('/presets').get_json()
    assert preset == {'name': 'A3 poster', 'options': {
        'outputsize': 'a3', 'pageorientation': 'landscape', 'device': 'hp7475a', 'speed': '20',
        'command_input': '', 'margin': '10', 'rotate': '90', 'linemerge': True, 'linesort': True,
        'linesimplify': False, 'reloop': False, 'mirror_x': True, 'mirror_y': False}}


def test_a_file_is_not_needed(client):
    assert 'file' not in FORM and save(client).status_code == 200


def test_presets_are_listed_by_name(client):
    for name in ('b', 'C', 'a'):
        save(client, name)
    assert [p['name'] for p in client.get('/presets').get_json()] == ['a', 'b', 'C']


def test_saving_again_replaces_the_preset(client):
    save(client, 'Poster', outputsize='a4')
    save(client, 'Poster', outputsize='a2')
    [preset] = client.get('/presets').get_json()
    assert preset['options']['outputsize'] == 'a2'


def test_presets_survive_a_restart(app, client):
    save(client, 'Poster')
    app.presets.init()                 # what starting the server does
    assert [p['name'] for p in client.get('/presets').get_json()] == ['Poster']


def test_a_preset_fills_the_form_back_in(app, client, uploads):
    """What is stored must be accepted again by the conversion it came from."""
    save(client, 'Poster', margin='2.5', rotate='270', mirror_y='on', reloop='on', command_input='')
    options = client.get('/presets').get_json()[0]['options']
    form = {k: ('on' if v is True else v) for k, v in options.items() if v is not False}
    parsed, error = app.main.conversion_options(form)
    assert error is None and parsed['margin'] == 2.5 and parsed['rotate'] == 270
    assert parsed['mirror_y'] and parsed['reloop'] and not parsed['mirror_x']


def test_a_custom_command_is_kept(client):
    save(client, 'Custom', command_input='linesort linemerge --tolerance 0.2mm')
    assert client.get('/presets').get_json()[0]['options']['command_input'] == 'linesort linemerge --tolerance 0.2mm'


@pytest.mark.parametrize('name', ['', ' ', 'x' * 41, '../x', 'a/b', '.hidden', '-x', 'a;b', 'a\nb', '<b>', 'ab"', "a'b", '١'])
def test_bad_names_are_refused(client, name):
    assert save(client, name).status_code == 400
    assert client.get('/presets').get_json() == []


@pytest.mark.parametrize('name', ['a', 'A3 poster (landscape)', 'x' * 40, 'v1.2_final', 'pen+ink-2'])
def test_good_names(client, name):
    assert save(client, name).status_code == 200


def test_names_have_their_spaces_trimmed(client):
    save(client, '  Poster  ')
    assert client.get('/presets').get_json()[0]['name'] == 'Poster'


@pytest.mark.parametrize('field,value', [('margin', '99'), ('rotate', '45'), ('outputsize', 'a9'),
                                         ('command_input', 'eval x'), ('speed', '1; rm')])
def test_options_are_validated_like_a_conversion(client, field, value):
    assert save(client, **{field: value}).status_code == 400
    assert client.get('/presets').get_json() == []


def test_there_is_a_limit(app, client):
    for n in range(app.presets.MAX_PRESETS):
        assert save(client, 'p%d' % n).status_code == 200
    response = save(client, 'one too many')
    assert response.status_code == 400 and b'Delete one first' in response.data
    assert save(client, 'p0', outputsize='a3').status_code == 200          # replacing is still fine


def test_delete(client):
    save(client, 'Poster')
    save(client, 'Other')
    assert client.post('/presets/delete', data={'name': 'Poster'}).data == b'Deleted preset Poster'
    assert [p['name'] for p in client.get('/presets').get_json()] == ['Other']
    assert client.post('/presets/delete', data={'name': 'Poster'}).status_code == 404
    assert client.post('/presets/delete', data={}).status_code == 404


def test_changing_presets_is_post_only(client):
    assert client.put('/presets').status_code == 405
    assert client.get('/presets/delete').status_code == 405


def test_a_broken_database_does_not_raise(app, client, monkeypatch, tmp_path):
    monkeypatch.setattr(app.history, 'DB_PATH', str(tmp_path / 'missing_dir' / 'history.db'))
    assert client.get('/presets').get_json() == []
    assert save(client).status_code == 400
    assert client.post('/presets/delete', data={'name': 'x'}).status_code == 404
    app.presets.init()
