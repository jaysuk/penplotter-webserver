import sqlite3

import pytest

OLD_SCHEMA = '''CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file TEXT NOT NULL,
    port TEXT,
    baudrate INTEGER,
    flow_control TEXT,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL,
    progress INTEGER DEFAULT 0,
    error TEXT)'''


def plot(history, estimate, drawn, status='completed'):
    job = history.start('x.hpgl', '/dev/x', 9600, 'None')
    history.set_estimate(job, estimate)
    history.finish(job, status, 100, drawn_s=drawn)
    return job


def test_an_old_database_gets_the_new_columns(app, tmp_path, monkeypatch):
    path = str(tmp_path / 'old.db')
    conn = sqlite3.connect(path)
    conn.execute(OLD_SCHEMA)
    conn.execute("INSERT INTO jobs (file, started_at, status) VALUES ('old.hpgl', 1, 'completed')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(app.history, 'DB_PATH', path)
    app.history.init()
    app.history.init()                      # and a second start must not try to add them again

    [job] = app.history.recent()
    assert job['file'] == 'old.hpgl' and job['estimate_s'] is None and job['drawn_s'] is None
    plot(app.history, 60, 90)
    assert app.history.recent()[0]['estimate_s'] == 60 and app.history.recent()[0]['drawn_s'] == 90


def test_no_correction_without_finished_plots(app):
    assert app.history.correction() == 1.0


def test_correction_is_the_median_ratio(app):
    for estimate, drawn in ((100, 150), (100, 200), (100, 130)):
        plot(app.history, estimate, drawn)
    assert app.history.correction() == pytest.approx(1.5)
    plot(app.history, 100, 300)             # an even number of plots: the middle two
    assert app.history.correction() == pytest.approx(1.75)


def test_only_recent_plots_count(app):
    for _ in range(10):
        plot(app.history, 100, 200)
    for _ in range(10):
        plot(app.history, 100, 100)
    assert app.history.correction(limit=10) == 1.0


def test_stopped_and_failed_plots_say_nothing_about_speed(app):
    plot(app.history, 100, 20, status='stopped')
    plot(app.history, 100, 10, status='failed')
    plot(app.history, None, 50)              # no estimate (the file could not be analysed)
    plot(app.history, 100, None)             # no drawing time
    assert app.history.correction() == 1.0


@pytest.mark.parametrize('drawn,expected', [(1, 0.3), (10000, 5.0)])
def test_correction_stays_within_bounds(app, drawn, expected):
    plot(app.history, 100, drawn)
    assert app.history.correction() == expected


def test_correction_survives_a_broken_database(app, tmp_path, monkeypatch):
    monkeypatch.setattr(app.history, 'DB_PATH', str(tmp_path / 'missing_dir' / 'history.db'))
    assert app.history.correction() == 1.0
    app.history.set_estimate(1, 10)         # must not raise either
