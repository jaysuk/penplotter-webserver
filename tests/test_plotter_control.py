"""Moving the pen by hand: the HPGL that is built and the routes that send it."""
import pytest

FORM = dict(port='/dev/ttyAMA0', baudrate='9600', flowControl='CTS/RTS')

# Digits of other scripts: Python's \d and int()/float() accept them, a plotter would not
ARABIC_INDIC_12 = '١٢'
ARABIC_INDIC_9600 = '٩٦٠٠'


def written(app):
    return app.serial.Serial.instances[-1].written


# ---- the commands -------------------------------------------------------------------------------

def test_millimetres_become_plotter_units(app):
    c = app.control
    assert c.units(10) == 402                      # 0.02488 mm per unit
    assert c.jog(10, -5) == [b'PU;PR402,-201;PA;']
    assert c.go_to(1, 2) == [b'PU;PA40,80;']
    assert c.origin() == [b'PU;PA0,0;'] and c.pen_up() == [b'PU;'] and c.pen_down() == [b'PD;']


@pytest.mark.parametrize('text,mm', [('5', 5), ('-12.5', -12.5), ('0', 0), ('999.99', 999.99), ('-0.1', -0.1)])
def test_distances(app, text, mm):
    assert app.control.parse_mm(text) == mm


@pytest.mark.parametrize('text', ['', 'a', '1e3', '1000', '1.234', '--1', '+5', ' 5', '5 ', '5\n',
                                  ARABIC_INDIC_12, None, 5])
def test_bad_distances(app, text):
    assert app.control.parse_mm(text) is None


def test_pens(app):
    assert app.control.select_pen(0) == [b'PU;SP0;'] and app.control.select_pen(8) == [b'PU;SP8;']
    for bad in (-1, 9, '1', None, 1.0):
        assert app.control.select_pen(bad) is None


def test_trace_bounds(app):
    corners = [b'PU;PA100,200;', b'PA900,200;', b'PA900,700;', b'PA100,700;', b'PA100,200;', b'PU;']
    assert app.control.trace_bounds([100, 200, 900, 700]) == corners
    drawn = app.control.trace_bounds([100.4, 200, 900, 700], draw=True)
    assert drawn[:3] == [b'PU;PA100,200;', b'PD;', b'PA900,200;'] and drawn[-1] == b'PU;'


def test_position_answer(app):
    assert app.control.parse_position('4019,-2009,1\r') == {'x_mm': 100.0, 'y_mm': -50.0, 'pen_down': True}
    assert app.control.parse_position('0,0,0') == {'x_mm': 0.0, 'y_mm': 0.0, 'pen_down': False}
    for bad in ('', 'x', '1,2', '1,2,3,4', '1.5,2,0'):
        assert app.control.parse_position(bad) is None


# ---- the routes ---------------------------------------------------------------------------------

def test_jog(app, client):
    assert client.post('/plotter/jog', data={**FORM, 'dx': '10', 'dy': '-5'}).data == b'OK'
    port = app.serial.Serial.instances[-1]
    assert port.written == [b'PU;PR402,-201;PA;'] and port.closed
    assert not app.main.plot_lock.locked()


@pytest.mark.parametrize('flow', ['CTS/RTS', 'Software', 'XON/XOFF', 'None'])
def test_the_plotter_is_not_reset(app, client, flow):
    """Moving the pen must not send IN; or the handshake set-up that a plot starts with."""
    client.post('/plotter/pen_up', data={**FORM, 'flowControl': flow})
    assert written(app) == [b'PU;']
    assert app.serial.Serial.instances[-1].kwargs.get('xonxoff', False) == (flow == 'XON/XOFF')


@pytest.mark.parametrize('action,form,expected', [
    ('pen_up', {}, [b'PU;']),
    ('pen_down', {}, [b'PD;']),
    ('origin', {}, [b'PU;PA0,0;']),
    ('select_pen', {'pen': '3'}, [b'PU;SP3;']),
    ('select_pen', {'pen': '0'}, [b'PU;SP0;']),
])
def test_pen_actions(app, client, action, form, expected):
    assert client.post('/plotter/' + action, data={**FORM, **form}).data == b'OK'
    assert written(app) == expected


@pytest.mark.parametrize('action,form', [
    ('jog', {}), ('jog', {'dx': '1'}), ('jog', {'dx': 'x', 'dy': '1'}), ('jog', {'dx': '1000', 'dy': '1'}),
    ('jog', {'dx': ARABIC_INDIC_12, 'dy': '1'}),
    ('select_pen', {}), ('select_pen', {'pen': '9'}), ('select_pen', {'pen': '²'}), ('select_pen', {'pen': '-1'}),
    ('bounds', {}), ('bounds', {'file': '../x.hpgl'}), ('bounds', {'file': 'missing.hpgl'}),
    ('format_disk', {}),
])
def test_bad_requests(app, client, action, form):
    assert client.post('/plotter/' + action, data={**FORM, **form}).status_code == 400
    assert app.serial.Serial.instances == []          # nothing was sent


@pytest.mark.parametrize('field,value', [('port', 'COM'), ('port', '/etc/passwd'), ('port', ''),
                                         ('baudrate', '12'), ('baudrate', 'x'), ('baudrate', '²'),
                                         ('baudrate', ARABIC_INDIC_9600), ('flowControl', 'magic')])
def test_bad_serial_settings(app, client, field, value):
    assert client.post('/plotter/pen_up', data={**FORM, field: value}).status_code == 400
    assert app.serial.Serial.instances == []


def test_state_changing_routes_are_post_only(client):
    assert client.get('/plotter/pen_up').status_code == 405


def test_a_plot_owns_the_port(app, client):
    app.main.plot_lock.acquire()
    try:
        response = client.post('/plotter/pen_up', data=FORM)
        assert response.status_code == 409 and app.serial.Serial.instances == []
    finally:
        app.main.plot_lock.release()
    assert client.post('/plotter/pen_up', data=FORM).status_code == 200      # and it is freed again


def test_port_that_cannot_be_opened(app, client):
    app.serial.Serial.fail_open = True
    response = client.post('/plotter/pen_up', data=FORM)
    assert response.status_code == 500 and b'could not open port' in response.data
    assert not app.main.plot_lock.locked()


def test_position(app, client):
    assert client.post('/plotter/position', data=FORM).get_json() == {'x_mm': 100.0, 'y_mm': -50.0, 'pen_down': True}
    assert app.serial.Serial.instances[-1].written == [b'OA;'] and app.serial.Serial.instances[-1].closed


def test_position_when_the_plotter_is_silent(app, client):
    app.serial.Serial.no_reply = True
    assert client.post('/plotter/position', data=FORM).status_code == 504
    assert app.serial.Serial.instances[-1].closed and not app.main.plot_lock.locked()


def test_trace_the_plot_area(app, client, uploads):
    (uploads / 'a.hpgl').write_bytes(b'IN;SP1;PU400,800;PD2400,800,2400,2800,400,2800,400,800;PU;')
    assert client.post('/plotter/bounds', data={**FORM, 'file': 'a.hpgl'}).data == b'OK'
    assert written(app) == [b'PU;PA400,800;', b'PA2400,800;', b'PA2400,2800;', b'PA400,2800;', b'PA400,800;', b'PU;']
    assert client.post('/plotter/bounds', data={**FORM, 'file': 'a.hpgl', 'draw': '1'}).data == b'OK'
    assert b'PD;' in written(app)


def test_trace_refuses_a_file_that_draws_nothing(app, client, uploads):
    (uploads / 'a.hpgl').write_bytes(b'IN;SP1;PU400,800;PU;')
    assert client.post('/plotter/bounds', data={**FORM, 'file': 'a.hpgl'}).status_code == 400


def test_trace_uses_the_plots_real_area(app, client, uploads):
    """Layers drawn in relative mode (as vpype writes them) are traced where they land."""
    (uploads / 'a.hpgl').write_bytes(b'IN;SP1;PU1000,1000;PR;PD400,0,0,400;PA;PU;')
    client.post('/plotter/bounds', data={**FORM, 'file': 'a.hpgl'})
    assert b'PA1400,1400;' in written(app)
