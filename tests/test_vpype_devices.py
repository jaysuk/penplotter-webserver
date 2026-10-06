"""Plotter devices of your own for vpype: checking, storing, the routes, backups and a real conversion."""
import importlib.util
import os

import pytest

from test_backup import download, make_zip, restore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GOOD = '''
[device.mp4200]
name = "Graptech MP4200"
plotter_unit_length = "0.025mm"
pen_count = 2

[[device.mp4200.paper]]
name = "a4"
paper_size = ["297mm", "210mm"]
x_range = [0, 11880]
y_range = [0, 8400]
y_axis_up = true
origin_location = ["0mm", "0mm"]
origin_location_reference = "botleft"
'''

QUICK = {'name': 'My Plotter', 'unit': '0.025', 'pens': '4', 'origin': 'botleft', 'origin_x': '0', 'origin_y': '0',
         'sizes': ['a4', 'a3']}


def with_change(old, new, text=GOOD):
    assert old in text
    return text.replace(old, new)


# ---- checking what comes in ----------------------------------------------------------------------

def test_a_device_in_vpypes_format_is_accepted(app):
    devices = app.devices.parse(GOOD)
    assert list(devices) == ['mp4200']
    device = devices['mp4200']
    assert device['pen_count'] == 2 and device['plotter_unit_length'] == '0.025mm'
    assert device['paper'][0]['x_range'] == [0, 11880] and device['paper'][0]['y_axis_up'] is True


def test_the_text_written_is_read_back_the_same(app):
    devices = app.devices.parse(GOOD)
    assert app.devices.parse(app.devices.to_toml(devices)) == devices


@pytest.mark.parametrize('old,new', [
    ('pen_count = 2', 'pen_count = 0'),
    ('pen_count = 2', 'pen_count = "2"'),
    ('pen_count = 2', 'pen_count = true'),
    ('0.025mm', '0.025'),
    ('0.025mm', '0mm'),
    ('0.025mm', '99mm'),
    ('0.025mm', '1e-2mm'),
    ('[0, 11880]', '[11880, 0]'),
    ('[0, 11880]', '[0, 1e9]'),
    ('[0, 11880]', '[0]'),
    ('y_axis_up = true', 'y_axis_up = "yes"'),
    ('y_axis_up = true\n', ''),
    ('origin_location = ["0mm", "0mm"]\n', ''),
    ('"0mm", "0mm"', '"0mm", "0lightyears"'),
    ('"botleft"', '"middle"'),
    ('name = "a4"', 'name = "a 4"'),
    ('name = "a4"', 'name = ""'),
    ('paper_size = ["297mm", "210mm"]', 'paper_size = ["0mm", "210mm"]'),
    ('pen_count = 2', 'pen_count = 2\nsurprise = 1'),
    ('origin_location_reference', 'origin_location_refrence'),
    ('Graptech MP4200', '<b>Graptech</b>'),
])
def test_a_device_vpype_could_not_use_is_refused(app, old, new):
    with pytest.raises(app.devices.DeviceError):
        app.devices.parse(GOOD.replace(old, new, 1))


@pytest.mark.parametrize('ident', ['MP4200', '4200', 'a' * 31, 'mp-4200', 'mp 4200'])
def test_a_device_id_must_be_plain(app, ident):
    quoted = '"{}"'.format(ident)
    text = GOOD.replace('[device.mp4200]', '[device.{}]'.format(quoted)).replace(
        '[[device.mp4200.paper]]', '[[device.{}.paper]]'.format(quoted))
    with pytest.raises(app.devices.DeviceError):
        app.devices.parse(text)


def test_only_devices_are_read_from_a_file(app):
    for text in ('[pen_config.rgb]\nlayers = []\n', GOOD + '\n[command.write]\nx = 1\n', 'x = 1'):
        with pytest.raises(app.devices.DeviceError):
            app.devices.parse(text)


def test_text_that_is_not_toml_or_too_large_is_refused(app):
    with pytest.raises(app.devices.DeviceError):
        app.devices.parse('this is [not toml')
    with pytest.raises(app.devices.DeviceError):
        app.devices.parse(b'\xff\xfe')
    with pytest.raises(app.devices.DeviceError):
        app.devices.parse(GOOD + '# ' + 'x' * app.devices.MAX_BYTES)


def test_a_paper_name_cannot_be_used_twice(app):
    twice = GOOD + '\n' + GOOD[GOOD.index('[[device.mp4200.paper]]'):]
    with pytest.raises(app.devices.DeviceError):
        app.devices.parse(twice)


def test_the_names_of_vpypes_devices_are_reserved(app):
    with pytest.raises(app.devices.DeviceError):
        app.devices.parse(GOOD, reserved={'mp4200'})


def test_a_paper_of_any_size_needs_an_orientation(app):
    flex = with_change('paper_size = ["297mm", "210mm"]', 'paper_orientation = "landscape"')
    assert app.devices.parse(flex)['mp4200']['paper'][0]['paper_orientation'] == 'landscape'
    with pytest.raises(app.devices.DeviceError):
        app.devices.parse(with_change('paper_size = ["297mm", "210mm"]\n', ''))


def test_strings_are_written_as_toml_strings(app):
    devices = app.devices.parse(with_change('Graptech MP4200', 'Quote \\" and \\\\ and \\n line'))
    assert app.devices.parse(app.devices.to_toml(devices))['mp4200']['name'] == devices['mp4200']['name']
    assert '"' in devices['mp4200']['name'] and '\\' in devices['mp4200']['name']


# ---- the quick form ------------------------------------------------------------------------------

def test_the_quick_form_makes_a_device(app):
    text = app.devices.generate(QUICK)
    device = app.devices.parse(text)['my_plotter']
    assert device['name'] == 'My Plotter' and device['pen_count'] == 4
    a4, a3 = device['paper']
    assert a4['name'] == 'a4' and a4['paper_size'] == ['297mm', '210mm']
    assert a4['x_range'] == [0, 11880] and a4['y_range'] == [0, 8400]
    assert a4['y_axis_up'] is True and a4['origin_location_reference'] == 'botleft'
    assert a3['x_range'] == [0, 16800]


def test_the_offset_of_the_origin_shortens_the_range(app):
    text = app.devices.generate(dict(QUICK, origin_x='10', origin_y='5', sizes=['a4']))
    paper = app.devices.parse(text)['my_plotter']['paper'][0]
    assert paper['origin_location'] == ['10mm', '5mm']
    assert paper['x_range'] == [0, 11480] and paper['y_range'] == [0, 8200]


def test_an_origin_at_the_top_counts_down(app):
    paper = app.devices.parse(app.devices.generate(dict(QUICK, origin='topleft', rotate_180='on')))['my_plotter']['paper'][0]
    assert paper['y_axis_up'] is False and 'origin_location_reference' not in paper and paper['rotate_180'] is True


@pytest.mark.parametrize('change', [
    {'name': ''}, {'name': '<b>'}, {'name': 'x' * 61}, {'unit': '0'}, {'unit': 'abc'}, {'unit': '11'}, {'pens': '0'},
    {'pens': '2.5'}, {'pens': '100'}, {'origin': 'middle'}, {'origin_x': '-1'}, {'origin_x': '101'}, {'sizes': []},
    {'sizes': ['a5']},
])
def test_the_quick_form_refuses_what_it_cannot_use(app, change):
    with pytest.raises(app.devices.DeviceError):
        app.devices.generate(dict(QUICK, **change))


def test_a_name_without_letters_still_gets_an_id(app):
    assert list(app.devices.parse(app.devices.generate(dict(QUICK, name='7475')))) == ['device_7475']


# ---- storing them --------------------------------------------------------------------------------

def test_devices_are_kept_in_userdata_and_listed(app, client):
    assert client.get('/vpype_devices').get_json()['devices'] == []
    response = client.post('/vpype_devices', data={'text': GOOD})
    assert response.status_code == 200 and response.get_json() == {'saved': ['mp4200']}
    assert os.path.isfile(os.path.join('userdata', 'vpype_devices.toml'))
    listed = client.get('/vpype_devices').get_json()
    assert [d['id'] for d in listed['devices']] == ['mp4200']
    assert listed['devices'][0]['papers'] == ['a4'] and listed['devices'][0]['pens'] == 2
    assert app.devices.parse(listed['devices'][0]['text']) == app.devices.parse(GOOD)


def test_saving_again_replaces_and_a_rename_removes_the_old_one(app, client):
    client.post('/vpype_devices', data={'text': GOOD})
    client.post('/vpype_devices', data={'text': with_change('pen_count = 2', 'pen_count = 3')})
    assert [d['pens'] for d in client.get('/vpype_devices').get_json()['devices']] == [3]
    renamed = GOOD.replace('mp4200', 'mp4200b')
    client.post('/vpype_devices', data={'text': renamed, 'replace': 'mp4200'})
    assert [d['id'] for d in client.get('/vpype_devices').get_json()['devices']] == ['mp4200b']


def test_a_bad_device_is_refused_with_the_reason(app, client):
    response = client.post('/vpype_devices', data={'text': with_change('pen_count = 2', 'pen_count = 0')})
    assert response.status_code == 400 and 'pen_count' in response.get_data(as_text=True)
    assert client.post('/vpype_devices', data={'text': ''}).status_code == 400
    assert client.get('/vpype_devices').get_json()['devices'] == []


def test_a_device_is_deleted(app, client):
    client.post('/vpype_devices', data={'text': GOOD})
    assert client.post('/vpype_devices/delete', data={'id': 'nope'}).status_code == 404
    assert client.post('/vpype_devices/delete', data={'id': 'mp4200'}).status_code == 200
    assert client.get('/vpype_devices').get_json()['devices'] == []


def test_a_damaged_file_is_left_alone(app, client):
    os.makedirs('userdata', exist_ok=True)
    with open(os.path.join('userdata', 'vpype_devices.toml'), 'w') as f:
        f.write('not [ valid')
    assert client.get('/vpype_devices').get_json()['devices'] == []
    response = client.post('/vpype_devices', data={'text': GOOD})
    assert response.status_code == 400 and 'cannot be read' in response.get_data(as_text=True)
    with open(os.path.join('userdata', 'vpype_devices.toml')) as f:
        assert f.read() == 'not [ valid'


def test_the_quick_form_route_gives_text_to_save(app, client):
    response = client.post('/vpype_devices/generate', data=dict(QUICK, sizes=['a4', 'a3']))
    assert response.status_code == 200
    assert client.post('/vpype_devices', data={'text': response.get_json()['text']}).status_code == 200
    assert client.post('/vpype_devices/generate', data=dict(QUICK, pens='0')).status_code == 400


def test_a_device_of_vpype_is_a_start_for_one_of_your_own(app, client):
    pytest.importorskip('vpype')
    response = client.get('/vpype_devices/template?base=hp7475a')
    assert response.status_code == 200
    text = response.get_json()['text']
    assert '[device.hp7475a_copy]' in text
    assert client.post('/vpype_devices', data={'text': text}).status_code == 200
    assert client.get('/vpype_devices/template?base=nope').status_code == 404
    assert client.get('/vpype_devices/template?base=hp7475a_copy').status_code == 404     # not vpype's own


# ---- using them ----------------------------------------------------------------------------------

def convert_form(device, **extra):
    return dict({'file': 'a.svg', 'device': device, 'outputsize': 'a4', 'pageorientation': 'landscape'}, **extra)


def test_a_device_of_your_own_can_be_converted_for(app, client, uploads):
    (uploads / 'a.svg').write_text('<svg/>')
    assert client.post('/start_conversion', data=convert_form('plottertwo')).status_code == 400
    client.post('/vpype_devices', data={'text': GOOD.replace('mp4200', 'plottertwo')})
    response = client.post('/start_conversion', data=convert_form('plottertwo'))
    assert response.status_code == 200
    assert app.convert_stub.calls[-1][0][3] == 'plottertwo'


def test_a_paper_size_the_device_lacks_is_explained(app, client, uploads):
    (uploads / 'a.svg').write_text('<svg/>')
    client.post('/vpype_devices', data={'text': GOOD})
    response = client.post('/start_conversion', data=convert_form('mp4200', outputsize='a3'))
    assert response.status_code == 400
    message = response.get_data(as_text=True)
    assert 'A3' in message and 'a4' in message


def test_a_plotter_profile_can_use_a_device_of_your_own(app, client):
    mine = {'name': 'Mine', 'device': 'plottertwo', 'baudrate': '9600', 'flowControl': 'CTS/RTS', 'pen_change': 'auto'}
    response = client.post('/plotters', data=mine)
    assert response.status_code == 400 and 'plottertwo' in response.get_data(as_text=True)      # not added yet
    client.post('/vpype_devices', data={'text': GOOD.replace('mp4200', 'plottertwo')})
    assert client.post('/plotters', data=mine).status_code == 200
    assert app.plotters.clean_settings({'device': 'MP 4200'})[1] == 'Invalid plotter device'


def test_plotters_and_their_devices_come_back_from_one_backup(app, client):
    mine = {'name': 'Mine', 'device': 'plottertwo', 'baudrate': '9600', 'flowControl': 'CTS/RTS', 'pen_change': 'auto'}
    client.post('/vpype_devices', data={'text': GOOD.replace('mp4200', 'plottertwo')})
    client.post('/plotters', data=mine)
    archive, _ = download(client)
    members = {name: archive.read(name) for name in ('plotters.json', 'vpype_devices.toml')}
    client.post('/plotters/delete', data={'id': 'mine'})
    client.post('/vpype_devices/delete', data={'id': 'plottertwo'})

    # the plotter alone, with its device gone: refused
    refused = restore(client, make_zip({'plotters.json': members['plotters.json']}))
    assert refused.status_code == 400 and 'plottertwo' in refused.get_data(as_text=True)
    assert restore(client, make_zip(members)).status_code == 200
    assert [p['id'] for p in client.get('/plotters').get_json() if p['source'] == 'custom'] == ['mine']


def test_the_default_device_may_be_one_of_your_own(app):
    valid = app.main.CONFIG_FIELDS['plotter_device'][2]
    assert valid('mp4200') and not valid('../etc')


# ---- backups -------------------------------------------------------------------------------------

def test_the_devices_are_in_a_backup_and_come_back(app, client):
    client.post('/vpype_devices', data={'text': GOOD})
    archive, _ = download(client)
    assert 'vpype_devices.toml' in archive.namelist()
    data = archive.read('vpype_devices.toml')

    client.post('/vpype_devices/delete', data={'id': 'mp4200'})
    response = restore(client, make_zip({'vpype_devices.toml': data}))
    assert response.status_code == 200 and response.get_json()['devices'] == 1
    assert [d['id'] for d in client.get('/vpype_devices').get_json()['devices']] == ['mp4200']


def test_a_backup_with_a_bad_device_restores_nothing(app, client):
    client.post('/vpype_devices', data={'text': GOOD})
    response = restore(client, make_zip({'vpype_devices.toml': with_change('pen_count = 2', 'pen_count = 0')}))
    assert response.status_code == 400
    assert [d['pens'] for d in client.get('/vpype_devices').get_json()['devices']] == [2]


# ---- vpype itself --------------------------------------------------------------------------------

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100">'
       '<path d="M10,10 L90,10 L90,90 L10,90 Z" fill="none" stroke="black"/></svg>')


@pytest.fixture
def real_convert(app):
    pytest.importorskip('vpype')
    pytest.importorskip('vpype_cli')
    spec = importlib.util.spec_from_file_location('convert_vpype_real', os.path.join(ROOT, 'convert_vpype.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (app.dir / 'uploads' / 'a.svg').write_text(SVG)
    yield module
    app.devices.register()      # forget the devices of the test


def hpgl(app, name):
    with open(os.path.join(app.dir, 'uploads', name)) as f:
        return f.read()


def test_vpype_converts_for_a_device_of_your_own(app, client, real_convert):
    client.post('/vpype_devices', data={'text': GOOD})
    message = real_convert.convert_file('uploads/a.svg', 'a4', 'landscape', 'mp4200')
    assert message.startswith('Exported'), message
    text = hpgl(app, 'a-a4-landscape-mp4200.hpgl')
    assert text.startswith('IN;DF;SP1;') and text.endswith('SP0;IN;\n')


def test_an_edited_or_deleted_device_is_seen_by_the_next_conversion(app, client, real_convert):
    client.post('/vpype_devices', data={'text': GOOD})
    assert real_convert.convert_file('uploads/a.svg', 'a4', 'landscape', 'mp4200').startswith('Exported')
    first = hpgl(app, 'a-a4-landscape-mp4200.hpgl')
    # moving the origin moves the drawing
    client.post('/vpype_devices', data={'text': with_change('origin_location = ["0mm", "0mm"]', 'origin_location = ["20mm", "0mm"]')})
    assert real_convert.convert_file('uploads/a.svg', 'a4', 'landscape', 'mp4200').startswith('Exported')
    assert hpgl(app, 'a-a4-landscape-mp4200.hpgl') != first
    client.post('/vpype_devices/delete', data={'id': 'mp4200'})
    assert real_convert.convert_file('uploads/a.svg', 'a4', 'landscape', 'mp4200') == 'File not converted.'


def test_a_device_of_the_quick_form_converts(app, client, real_convert):
    text = client.post('/vpype_devices/generate', data=QUICK).get_json()['text']
    assert client.post('/vpype_devices', data={'text': text}).status_code == 200
    assert real_convert.convert_file('uploads/a.svg', 'a3', 'portrait', 'my_plotter').startswith('Exported')


def test_the_devices_vpype_comes_with_are_not_taken_over(app, client, real_convert):
    response = client.post('/vpype_devices', data={'text': GOOD.replace('mp4200', 'hp7475a')})
    assert response.status_code == 400 and 'comes with vpype' in response.get_data(as_text=True)
