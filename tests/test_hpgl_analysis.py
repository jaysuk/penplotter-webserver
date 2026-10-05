import io
import math

import pytest

# vpype's output for two layers, relative coordinates (PR) and all
VPYPE_TWO_LAYERS = (b'IN;DF;VS10;PS4;SP1;PU3959,5667;PR;PD3215,0,0,-3215,-3215,0,0,3215;PU;'
                    b'SP2;PU804,-804;PD1608,0,0,-1607,-1608,0,0,1607;PA;PU11040,7721;SP0;IN;')

# No acceleration, pen lifts or per move overhead: times are just distance / speed
SIMPLE = {'vector_s': 0, 'travel_move_s': 0, 'pen_lift_s': 0, 'speed_cm_s': 10, 'travel_cm_s': 20}


@pytest.fixture
def hpgl(app):
    return app.hpgl


@pytest.fixture
def write(tmp_path):
    def make(content, name='t.hpgl'):
        path = tmp_path / name
        path.write_bytes(content)
        return str(path)
    return make


def test_commands_have_offsets(hpgl):
    data = b'IN;SP1;\nPU0,0;PD100,100;LB a;b\x03;'
    commands = list(hpgl.iter_commands(io.BytesIO(data)))
    assert [c[2] for c in commands] == ['IN', 'SP', 'PU', 'PD', 'LB']
    for start, end, code, args in commands:
        assert data[start:start + 2].upper() == code.encode()   # start is the first letter
        assert data[end - 1:end] in (b';', b'\n', b'\x03')
    assert commands[4][3] == b' a;b'         # a label may contain ;


def test_commands_across_read_chunks(hpgl):
    data = b'PU0,0;PD10,10,20,20;' * 50 + b'LB label;with semicolon\x03;PU;'
    whole = list(hpgl.iter_commands(io.BytesIO(data)))
    for chunk in (1, 3, 7, 64):
        assert list(hpgl.iter_commands(io.BytesIO(data), chunk=chunk)) == whole


def test_last_command_without_terminator(hpgl):
    assert [c[2] for c in hpgl.iter_commands(io.BytesIO(b'PU0,0;PD5,5'))] == ['PU', 'PD']


def test_distances_bounds_and_time(hpgl, write):
    # 400 units is 10 mm at 40 units per mm
    path = write(b'IN;SP1;PU0,0;PD400,0,400,300;PU800,300;PD1200,300;')
    a = hpgl.analyze(path, units_per_mm=40, model=SIMPLE)
    assert a['draw_length'] == 400 + 300 + 400
    assert a['travel_length'] == 400                  # from (400,300) to (800,300)
    assert a['paths'] == 2
    assert a['bounds'] == [0, 0, 1200, 300]
    # drawing 1100 units = 27.5 mm at 100 mm/s, travelling 400 units = 10 mm at 200 mm/s
    assert a['seconds'] == pytest.approx(0.275 + 0.05)


def test_speed_comes_from_vs(hpgl, write):
    slow = hpgl.analyze(write(b'VS5;PU0,0;PD400,0;'), units_per_mm=40, model=SIMPLE)
    fast = hpgl.analyze(write(b'VS20;PU0,0;PD400,0;'), units_per_mm=40, model=SIMPLE)
    assert slow['seconds'] == pytest.approx(10 / 50)
    assert fast['seconds'] == pytest.approx(10 / 200)


def test_relative_coordinates(hpgl, write):
    a = hpgl.analyze(write(b'PA0,0;PR;PD100,0,0,100;PA;PU0,0;'), units_per_mm=40, model=SIMPLE)
    assert a['relative'] is True
    assert a['draw_length'] == 200
    assert a['bounds'] == [0, 0, 100, 100]
    # PU0,0 is absolute again: travel from (100,100) back to the origin
    assert a['travel_length'] == pytest.approx(141.42, abs=0.01)


def test_pen_lifts_and_vectors_cost_time(hpgl, write):
    model = dict(SIMPLE, pen_lift_s=0.1, vector_s=0.01, travel_move_s=0.02)
    a = hpgl.analyze(write(b'PU0,0;PD0,0,40,0;PU;'), units_per_mm=40, model=model)
    # PU0,0: a pen-up move (0.02). PD: lift down (0.1), a zero length vector (0.01) and a 1 mm
    # vector at 100 mm/s (0.01 + 0.01). PU: lift up (0.1)
    assert a['seconds'] == pytest.approx(0.02 + 0.1 + 0.01 + 0.02 + 0.1)


def test_empty_and_pen_up_files(hpgl, write):
    a = hpgl.analyze(write(b'IN;SP1;PU100,100;PU200,200;'), units_per_mm=40, model=SIMPLE)
    assert a['bounds'] is None and a['draw_length'] == 0 and a['paths'] == 0
    assert hpgl.analyze(write(b'', 'e.hpgl'))['segments'] == []


def test_unsupported_drawing_commands_are_reported(hpgl, write):
    assert hpgl.analyze(write(b'IN;SP1;PU0,0;EA10,10;'))['unsupported'] == ['EA']


def test_segments_follow_the_pens(hpgl, write):
    path = write(VPYPE_TWO_LAYERS)
    a = hpgl.analyze(path)
    assert [(s['pen'], s['paths']) for s in a['segments']] == [(1, 1), (2, 1)]
    first, second = a['segments']
    assert VPYPE_TWO_LAYERS[first['start']:first['start'] + 3] == b'SP1'
    assert VPYPE_TWO_LAYERS[second['start']:second['start'] + 3] == b'SP2'
    assert first['end'] == second['start']
    assert second['end'] == VPYPE_TWO_LAYERS.index(b'SP0')      # SP0 closes the last segment
    assert [p['pen'] for p in hpgl.pens_used(a)] == [1, 2]
    assert a['relative'] is True


def test_pen_changes(hpgl, write):
    a = hpgl.analyze(write(b'SP1;PU0,0;PD10,10;SP2;PU0,0;PD5,5;SP2;PD9,9;SP0;SP2;PD1,1;SP1;PD2,2;'))
    # no change for the first pen, the same pen again, or reselecting it after SP0
    assert [pen for _, pen in hpgl.pen_changes(a)] == [2, 1]


def test_time_at_interpolates(hpgl):
    a = {'marks': [[0, 0.0], [100, 10.0], [200, 30.0]]}
    assert hpgl.time_at(a, 0) == 0 and hpgl.time_at(a, 50) == 5
    assert hpgl.time_at(a, 150) == 20 and hpgl.time_at(a, 999) == 30 and hpgl.time_at(a, -5) == 0


def test_marks_cover_the_whole_file(hpgl, write):
    path = write(b'PU0,0;PD400,400;' * 2000)
    a = hpgl.analyze(path, units_per_mm=40, model=SIMPLE)
    assert a['marks'][0] == [0, 0] and a['marks'][-1][0] == a['bytes']
    assert a['marks'][-1][1] == pytest.approx(a['seconds'])
    assert len(a['marks']) > 10
    assert hpgl.time_at(a, a['bytes'] // 2) == pytest.approx(a['seconds'] / 2, rel=0.01)


def test_filter_keeps_only_the_chosen_pens(hpgl, write, tmp_path):
    src = write(VPYPE_TWO_LAYERS)
    a = hpgl.analyze(src)
    dst = str(tmp_path / 'only2.hpgl')
    size = hpgl.filter_pens(src, dst, [2], a)
    out = open(dst, 'rb').read()
    assert size == len(out)
    assert b'SP1' not in out and b'SP2' in out and out.startswith(b'IN;DF;VS10;PS4;')
    assert out.endswith(b'SP0;IN;')
    # SP2 is preceded by a move to where layer 1 left the pen, in absolute coordinates, and the
    # file carries on in relative mode as it did before
    assert b'PU;PA3959,5667;PR;SP2;' in out
    where = hpgl.analyze(dst)
    assert [s['pen'] for s in where['segments']] == [2]
    assert where['draw_length'] == pytest.approx(a['segments'][1]['draw_length'])


def test_filtered_layer_is_drawn_in_the_same_place(hpgl, write, tmp_path):
    """Plot only layer 2 and it must land exactly where it would in the full plot."""
    src = write(VPYPE_TWO_LAYERS)
    a = hpgl.analyze(src)
    dst = str(tmp_path / 'only2.hpgl')
    hpgl.filter_pens(src, dst, [2], a)

    def drawn_points(path):
        points, state = [], hpgl.State()
        with open(path, 'rb') as f:
            for _, _, code, args in hpgl.iter_commands(f):
                for x0, y0, x1, y1, down in state.apply(code, args):
                    if down and state.pen == 2:
                        points.append((round(x1), round(y1)))
        return points

    assert drawn_points(dst) == drawn_points(src) != []


def test_filter_rejects_unused_pens(hpgl, write, tmp_path):
    src = write(VPYPE_TWO_LAYERS)
    with pytest.raises(ValueError):
        hpgl.filter_pens(src, str(tmp_path / 'x.hpgl'), [5], hpgl.analyze(src))


def test_cache_is_reused_until_the_file_changes(hpgl, write, monkeypatch):
    path = write(b'SP1;PU0,0;PD100,100;')
    first = hpgl.analyze_cached(path)
    calls = []
    real = hpgl.analyze
    monkeypatch.setattr(hpgl, 'analyze', lambda *a, **k: calls.append(1) or real(*a, **k))
    assert hpgl.analyze_cached(path) == first and calls == []
    open(path, 'ab').write(b'PD200,200;')
    assert hpgl.analyze_cached(path)['draw_length'] > first['draw_length'] and calls == [1]


def test_big_files_are_not_analysed(hpgl, write, monkeypatch):
    path = write(b'SP1;PU0,0;PD1,1;')
    monkeypatch.setattr(hpgl, 'MAX_ANALYSE_BYTES', 4)
    assert hpgl.analyze_cached(path) is None


def test_summary(hpgl, write):
    s = hpgl.summary(hpgl.analyze(write(VPYPE_TWO_LAYERS)), correction=2.0)
    assert [p['pen'] for p in s['pens']] == [1, 2] and s['width_mm'] > 0 and s['seconds'] > 0
    assert hpgl.summary(None) is None


# ---- circles and arcs -------------------------------------------------------------------------

def drawn_moves(hpgl, content):
    state, moves = hpgl.State(), []
    for _, _, code, args in hpgl.iter_commands(__import__('io').BytesIO(content)):
        moves += state.apply(code, args)
    return state, [m for m in moves if m[4]]


def test_a_circle_is_drawn_whatever_the_pen_state(hpgl):
    state, drawn = drawn_moves(hpgl, b'IN;SP1;PU100,100;CI50;')
    assert len(drawn) == 72                                  # 5 degree chords
    assert all(abs(math.hypot(m[2] - 100, m[3] - 100) - 50) < 1e-6 for m in drawn)
    assert (state.x, state.y) == (100, 100) and state.pen_down is False     # back at the centre, pen as it was


def test_circle_chord_angle(hpgl):
    _, drawn = drawn_moves(hpgl, b'PU0,0;CI10,30;')
    assert len(drawn) == 12


def test_an_arc_goes_counter_clockwise_from_the_pen_position(hpgl):
    state, drawn = drawn_moves(hpgl, b'PU50,0;AA0,0,90;')
    assert round(state.x, 6) == 0 and round(state.y, 6) == 50
    assert len(drawn) == 18
    state, _ = drawn_moves(hpgl, b'PU50,0;AA0,0,-90;')
    assert round(state.x, 6) == 0 and round(state.y, 6) == -50


def test_a_relative_arc_takes_its_centre_from_the_pen(hpgl):
    state, _ = drawn_moves(hpgl, b'PA100,0;AR-50,0,180;')    # centre at (50, 0)
    assert round(state.x, 6) == 0 and round(state.y, 6) == 0


def test_degenerate_curves_draw_nothing(hpgl):
    for content in (b'CI0;', b'CI;', b'CI-5;', b'PU5,5;AA5,5,90;', b'PU5,5;AA0,0,0;', b'AA1,1;'):
        assert drawn_moves(hpgl, content)[1] == []


def test_curves_count_towards_the_analysis(hpgl, write):
    result = hpgl.analyze(write(b'IN;SP1;PU0,0;CI50;'))
    assert result['unsupported'] == []
    assert result['draw_length'] == pytest.approx(2 * math.pi * 50, rel=0.01)
    assert result['bounds'][2] == pytest.approx(50, abs=1) and result['bounds'][3] == pytest.approx(50, abs=1)
    assert result['seconds'] > 0
