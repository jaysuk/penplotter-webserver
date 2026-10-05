"""Carrying on a plot that was stopped or failed part way."""
import pytest

from test_routes import PLOT, wait_for

FILE = b'IN;VS10;SP1;PU100,100;PD;PR10,0,0,10;PU;SP2;PA0,0;PD50,50;PU;SP0;'
# Where the commands start: IN; 0  VS10; 3  SP1; 8  PU100,100; 12  PD; 22  PR10,0,0,10; 25
# PU; 37  SP2; 40  PA0,0; 44  PD50,50; 50  PU; 58  SP0; 61


def offset_of(command):
    return FILE.index(command)


@pytest.fixture
def write(tmp_path):
    def make(content=FILE):
        path = tmp_path / 'a.hpgl'
        path.write_bytes(content)
        return str(path)
    return make


# ---- the preamble -----------------------------------------------------------------------------

def test_nothing_is_added_at_the_start_of_the_file(app, write):
    assert app.hpgl.resume_preamble(write(), 0) == (b'', 0)


def test_the_plotter_is_set_up_again_at_the_resume_point(app, write):
    preamble, start = app.hpgl.resume_preamble(write(), offset_of(b'SP2;'))
    assert start == offset_of(b'SP2;')
    # initialised, the speed and pen of the file so far, moved there with the pen up, relative mode again
    assert preamble == b'IN;VS10;SP1;PU;PA110,110;PR;'


def test_a_point_inside_a_command_goes_back_to_its_start(app, write):
    preamble, start = app.hpgl.resume_preamble(write(), offset_of(b'PR10') + 4)
    assert start == offset_of(b'PR10')
    assert preamble == b'IN;VS10;SP1;PU;PA100,100;PD;'     # the pen was down: it goes down again


def test_a_point_after_the_last_command_is_refused(app, write):
    with pytest.raises(ValueError):
        app.hpgl.resume_preamble(write(), len(FILE))


def test_setup_commands_are_repeated_but_not_drawing(app, write):
    content = b'IN;IP0,0,1000,1000;SC0,100,0,100;SP1;PU1,1;PD2,2;LB hello\x03PU;SP2;PU3,3;PD4,4;'
    preamble, start = app.hpgl.resume_preamble(write(content), content.index(b'SP2;'))
    assert preamble == b'IN;IP0,0,1000,1000;SC0,100,0,100;SP1;PU;PA2,2;'
    assert b'LB' not in preamble


def test_a_later_initialise_forgets_the_earlier_setup(app, write):
    content = b'IN;SC0,100,0,100;PU1,1;IN;PS4;SP1;PU5,5;PD6,6;PU;'
    preamble, _ = app.hpgl.resume_preamble(write(content), content.index(b'PD6'))
    assert preamble == b'IN;PS4;SP1;PU;PA5,5;'


def test_the_pen_is_never_deselected(app, write):
    content = b'IN;SP1;PU1,1;PD2,2;PU;SP0;PU9,9;PD;PU;'
    preamble, _ = app.hpgl.resume_preamble(write(content), content.index(b'PU9'))
    assert b'SP0' not in preamble and b'SP' not in preamble


def test_resume_file_is_the_preamble_and_the_rest(app, write, tmp_path):
    dst = str(tmp_path / 'resume.hpgl')
    start, skip = app.hpgl.resume_file(write(), dst, offset_of(b'SP2;') + 2)
    data = open(dst, 'rb').read()
    assert start == offset_of(b'SP2;')
    assert data[:skip] == b'IN;VS10;SP1;PU;PA110,110;PR;'
    assert data[skip:] == FILE[start:]


def test_the_resumed_file_draws_what_the_rest_of_the_original_draws(app, write, tmp_path):
    """Replaying the resume file ends where the whole original ends, and its drawing is a tail of it."""
    def drawn(path):
        state, lines = app.hpgl.State(), []
        with open(path, 'rb') as f:
            for _, _, code, args in app.hpgl.iter_commands(f):
                lines += [move for move in state.apply(code, args) if move[4]]
        return lines

    original = write()
    dst = str(tmp_path / 'resume.hpgl')
    app.hpgl.resume_file(original, dst, offset_of(b'PA0,0'))
    assert drawn(dst) == drawn(original)[-1:]
    assert len(drawn(original)) == 3


# ---- the sender reports how far it got --------------------------------------------------------

def test_the_sender_tracks_the_offset_it_reached(app, uploads):
    class SIO:
        def emit(self, *args, **kwargs):
            pass

    stopped = {'at': 20}

    def stop_after_a_few_chunks(port, data):
        stopped['at'] -= 1
        if stopped['at'] == 0:
            app.globals.printing = False

    app.serial.Serial.on_data = stop_after_a_few_chunks
    (uploads / 'big.hpgl').write_bytes(b'PU0,0;PD100,100;' * 5000)
    app.send2serial.sendToPlotter(SIO(), 'uploads/big.hpgl', '/dev/x', 9600, 'CTS/RTS')
    assert app.globals.sent_offset >= 30 and app.globals.sent_offset % 30 == 0      # whole 30 byte chunks
    assert app.globals.buffer_used == 30                  # the fake plotter reports an empty buffer: just this chunk

    app.globals.reset_plot_state()
    stopped['at'] = 20
    app.send2serial.sendToPlotter(SIO(), 'uploads/big.hpgl', '/dev/x', 9600, 'XON/XOFF')
    assert app.globals.sent_offset >= 30 and app.globals.sent_offset % 30 == 0
    assert app.globals.buffer_used == 0                   # no buffer feedback: the plotter finishes what it holds


# ---- resuming from the history ----------------------------------------------------------------

@pytest.fixture
def plotter(app, uploads, monkeypatch):
    """A sender that records the file it was given and reports how far it got."""
    state = {'sent': [], 'reached': 0, 'buffered': 0, 'result': False, 'paths': []}

    def fake_send(socketio, hpglfile, port, baud, flow, **kwargs):
        with open(hpglfile, 'rb') as f:
            state['sent'].append(f.read())
        state['paths'].append(hpglfile)
        app.globals.sent_offset = state['reached']
        app.globals.buffer_used = state['buffered']
        return state['result']

    monkeypatch.setattr(app.send2serial, 'sendToPlotter', fake_send)
    (uploads / 'a.hpgl').write_bytes(FILE)
    return state


def run_plot(app, client, route, data):
    assert client.post(route, data=data).data == b'Plot started'
    assert wait_for(lambda: not app.main.plot_lock.locked())


def history_rows(client):
    return client.get('/job_history').get_json()


def test_a_failed_plot_remembers_where_to_carry_on(app, client, plotter):
    plotter['reached'], plotter['buffered'] = 40, 5
    run_plot(app, client, '/start_plot', PLOT)
    [job] = history_rows(client)
    assert job['status'] == 'failed' and job['resume_offset'] == 35 and job['can_resume'] is True


def test_a_plot_that_sent_nothing_cannot_be_resumed(app, client, plotter):
    run_plot(app, client, '/start_plot', PLOT)
    assert history_rows(client)[0]['resume_offset'] is None
    assert history_rows(client)[0]['can_resume'] is False


def test_a_completed_plot_cannot_be_resumed(app, client, plotter):
    plotter['reached'], plotter['result'] = 40, True
    run_plot(app, client, '/start_plot', PLOT)
    job = history_rows(client)[0]
    assert job['status'] == 'completed' and job['can_resume'] is False
    assert client.post('/resume_job', data={'job': job['id']}).status_code == 400


def test_resuming_sends_the_rest_of_the_file(app, client, plotter):
    plotter['reached'] = 40
    run_plot(app, client, '/start_plot', PLOT)
    job = history_rows(client)[0]

    run_plot(app, client, '/resume_job', {'job': job['id']})
    resumed = plotter['sent'][1]
    assert resumed == b'IN;VS10;SP1;PU;PA110,110;PR;' + FILE[offset_of(b'SP2;'):]
    assert plotter['paths'][1].endswith('a-resume.hpgl')
    import os
    assert not os.path.exists(plotter['paths'][1])         # the copy is deleted afterwards
    rows = history_rows(client)
    assert len(rows) == 2 and rows[0]['file'] == 'a.hpgl'


def test_a_rewind_goes_back_further(app, client, plotter):
    plotter['reached'] = 45
    run_plot(app, client, '/start_plot', PLOT)
    job = history_rows(client)[0]
    run_plot(app, client, '/resume_job', {'job': job['id'], 'rewind': '10'})
    assert plotter['sent'][1].endswith(FILE[offset_of(b'PU;SP2'):])     # 45 - 10 = 35: back in the PR command


def test_a_resumed_plot_that_stops_again_counts_in_the_original_file(app, client, plotter):
    plotter['reached'] = 40
    run_plot(app, client, '/start_plot', PLOT)
    first = history_rows(client)[0]

    skip = len(b'IN;VS10;SP1;PU;PA110,110;PR;')
    plotter['reached'] = skip + 8                    # 8 bytes into the rest of the file (which starts at SP2;)
    run_plot(app, client, '/resume_job', {'job': first['id']})
    second, old = history_rows(client)
    assert second['resume_offset'] == offset_of(b'SP2;') + 8
    assert old['can_resume'] is False and old['resume_offset'] is None    # carried on: only the new one

    plotter['reached'] = 3                           # stopped again inside the set-up commands
    run_plot(app, client, '/resume_job', {'job': second['id']})
    # Resumed from byte 48, in the middle of the PA command at 44: that command is sent again, so
    # 44 is the furthest the plot is known to have got
    assert history_rows(client)[0]['resume_offset'] == offset_of(b'PA0,0')


def test_a_resume_that_sent_nothing_leaves_the_old_plot_resumable(app, client, plotter):
    plotter['reached'] = 40
    run_plot(app, client, '/start_plot', PLOT)
    first = history_rows(client)[0]
    plotter['reached'] = 0                           # e.g. the port could not be opened
    run_plot(app, client, '/resume_job', {'job': first['id']})
    assert [j['can_resume'] for j in history_rows(client)] == [False, True]


def test_resume_checks_its_input(app, client, plotter, uploads):
    plotter['reached'] = 40
    run_plot(app, client, '/start_plot', PLOT)
    job = history_rows(client)[0]['id']

    assert client.post('/resume_job', data={'job': 999}).status_code == 404
    assert client.post('/resume_job', data={'job': job, 'rewind': '-1'}).status_code == 400
    assert client.post('/resume_job', data={'job': job, 'rewind': 'x'}).status_code == 400
    assert client.post('/resume_job', data={'job': job, 'rewind': '99999999'}).status_code == 400
    assert client.get('/resume_job').status_code == 405

    (uploads / 'a.hpgl').write_bytes(FILE + b'PU;')        # the file changed since
    assert history_rows(client)[0]['can_resume'] is False
    assert client.post('/resume_job', data={'job': job}).status_code == 400
    (uploads / 'a.hpgl').unlink()
    assert client.post('/resume_job', data={'job': job}).status_code == 400


def test_resuming_a_plot_with_chosen_pens(app, client, plotter):
    """The offset is counted in the copy with only those pens, which is made again for the resume."""
    plotter['reached'] = 0
    run_plot(app, client, '/start_plot', dict(PLOT, pens='2'))
    pens_copy = plotter['sent'][0]
    assert b'SP1' not in pens_copy

    plotter['reached'] = pens_copy.index(b'PA0,0')
    run_plot(app, client, '/start_plot', dict(PLOT, pens='2'))
    job = history_rows(client)[0]
    assert job['resume_offset'] == pens_copy.index(b'PA0,0')

    run_plot(app, client, '/resume_job', {'job': job['id']})
    assert plotter['sent'][-1].endswith(pens_copy[pens_copy.index(b'PA0,0'):])
    assert b'SP1' not in plotter['sent'][-1]
