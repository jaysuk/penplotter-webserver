"""The form state kept on the server, so a reload or another device shows the same settings."""
import json
import os

import pytest

FILE = os.path.join('userdata', 'ui_state.json')


def post(client, data):
    return client.post('/ui_state', json=data)


def test_nothing_saved_gives_an_empty_state(app, client):
    assert client.get('/ui_state').get_json() == {}


def test_saved_values_come_back(app, client):
    state = {'plotter': {'device': 'dxy', 'baudrate': '4800', 'timelapse': True, '_profile': 'my-plotter'},
             'convert': {'margin': '5', 'mirror_x': False}}
    assert post(client, state).status_code == 200
    assert client.get('/ui_state').get_json() == state
    assert os.path.isfile(FILE)


def test_a_group_is_replaced_and_the_others_are_kept(app, client):
    post(client, {'plotter': {'device': 'dxy', 'baudrate': '4800'}, 'text': {'size': '20'}})
    post(client, {'plotter': {'device': 'hp7550'}})
    assert client.get('/ui_state').get_json() == {'plotter': {'device': 'hp7550'}, 'text': {'size': '20'}}


def test_numbers_are_kept_as_text(app, client):
    post(client, {'convert': {'margin': 5}})
    assert client.get('/ui_state').get_json() == {'convert': {'margin': '5'}}


@pytest.mark.parametrize('body', [
    None, [], {}, 'text', {'other': {}}, {'plotter': 'x'}, {'plotter': {'a b': 'x'}}, {'plotter': {'1a': 'x'}},
    {'plotter': {'a': None}}, {'plotter': {'a': ['x']}}, {'plotter': {'a': {'b': 'c'}}},
    {'plotter': {'a': 'x' * 501}}, {'plotter': {'a': 'one\ntwo'}}, {'plotter': {'a' * 41: 'x'}},
    {'plotter': {'f%d' % i: 'x' for i in range(81)}},
])
def test_bad_requests_are_refused_and_nothing_is_stored(app, client, body):
    assert post(client, body).status_code == 400
    assert not os.path.exists(FILE)


def test_a_damaged_file_is_ignored_and_replaced_by_the_next_save(app, client):
    os.makedirs('userdata', exist_ok=True)
    with open(FILE, 'w') as f:
        f.write('{not json')
    assert client.get('/ui_state').get_json() == {}
    assert post(client, {'plotter': {'device': 'dxy'}}).status_code == 200
    assert client.get('/ui_state').get_json() == {'plotter': {'device': 'dxy'}}


def test_what_is_on_disk_is_checked_like_a_request(app, client):
    os.makedirs('userdata', exist_ok=True)
    with open(FILE, 'w') as f:
        json.dump({'plotter': {'device': 'dxy'}, 'convert': {'bad key': 'x'}, 'other': {'a': 'b'}, 'text': 5}, f)
    assert client.get('/ui_state').get_json() == {'plotter': {'device': 'dxy'}}


def test_a_cross_site_save_is_refused(app, client):
    response = client.post('/ui_state', json={'plotter': {'device': 'dxy'}}, headers={'Origin': 'http://evil.example'})
    assert response.status_code == 403
    assert not os.path.exists(FILE)
