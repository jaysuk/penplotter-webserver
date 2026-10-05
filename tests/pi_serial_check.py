#!/usr/bin/env python3
"""Drive the real web plotter (real pyserial, real threads) against tests/sim_plotter.py.

Run it where the app's dependencies are installed (a Pi, or any Linux box):

    python3 tests/pi_serial_check.py

It starts a simulated plotter and a *separate* instance of the app on port 5099, in a temporary
directory, so nothing of a real install is touched. Prints one line per check and exits 1 if a
check failed. It checks behaviour that the fake serial port of the unit tests cannot: a real port
that really disappears, real timeouts and real buffering. It cannot say what a real plotter does.
"""
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PORT = 5099
BASE = 'http://127.0.0.1:{}'.format(PORT)

failures = []


def check(name, ok, detail=''):
    print('{}  {}{}'.format('ok  ' if ok else 'FAIL', name, ('  -- ' + str(detail)) if detail and not ok else ''), flush=True)
    if not ok:
        failures.append(name)


def call(path, data=None, method=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(BASE + path, data=body, method=method or ('POST' if data is not None else 'GET'))
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            text = response.read().decode()
            return response.status, text
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def status():
    return json.loads(call('/api/status')[1])


def wait(condition, timeout=30, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if condition():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def drawing(pens=2, loops=40):
    """An absolute-coordinate HPGL file with several pens and a few KB of strokes."""
    out = ['IN;VS20;']
    for pen in range(1, pens + 1):
        out.append('SP{};'.format(pen))
        for i in range(loops):
            cx, cy, r = 3000 + 700 * pen, 3000, 200 + i * 20
            points = ['{},{}'.format(round(cx + r * math.cos(a / 8)), round(cy + r * math.sin(a / 8))) for a in range(0, 51)]
            out.append('PU{};PD{};PU;'.format(points[0], ','.join(points[1:])))
    out.append('SP0;')
    return ''.join(out).encode()


class Sim:
    def __init__(self, folder, rate):
        self.log = os.path.join(folder, 'sim.log')
        self.pidfile = os.path.join(folder, 'sim.pid')
        self.process = subprocess.Popen([sys.executable, os.path.join(HERE, 'sim_plotter.py'), '--rate', str(rate),
                                         '--log', self.log, '--pidfile', self.pidfile],
                                        stdout=subprocess.PIPE, text=True)
        self.port = self.process.stdout.readline().strip()

    def signal(self, sig):
        os.kill(self.process.pid, sig)

    def text(self):
        with open(self.log) as f:
            return f.read()

    def plot_bytes(self):
        """What the plotter was given as plot data, in order."""
        out = bytearray()
        for line in self.text().splitlines():
            if ' DATA ' in line:
                out += line.split(' DATA ', 1)[1].replace('\\n', '\n').encode('latin-1')
        return bytes(out)

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(5)
        except subprocess.TimeoutExpired:
            self.process.kill()


def main():
    folder = tempfile.mkdtemp(prefix='piserial-')
    app_dir = os.path.join(folder, 'app')
    shutil.copytree(ROOT, app_dir, ignore=shutil.ignore_patterns(
        '.git', 'node_modules', '__pycache__', 'config.ini', 'history.db', 'uploads', 'cache', '.pytest_cache'))
    os.makedirs(os.path.join(app_dir, 'uploads'))
    server_log = open(os.path.join(folder, 'server.log'), 'w')
    server = subprocess.Popen(
        [sys.executable, '-c', 'import main; main.socketio.run(main.app, host="127.0.0.1", port={}, allow_unsafe_werkzeug=True)'.format(PORT)],
        cwd=app_dir, stdout=server_log, stderr=subprocess.STDOUT)
    sim = None
    try:
        if not wait(lambda: call('/api/status')[0] == 200, 60):
            print('the app did not start; see', os.path.join(folder, 'server.log'))
            return 1
        sim = Sim(folder, rate=2500)
        check('the simulated plotter has a port', sim.port.startswith('/dev/pts/'), sim.port)
        data = drawing()
        with open(os.path.join(app_dir, 'uploads', 'sim.hpgl'), 'wb') as f:
            f.write(data)
        print('file: {} bytes; plotter drains 2500 bytes a second'.format(len(data)))

        def form(**extra):
            return dict(dict(file='sim.hpgl', port=sim.port, baudrate='9600', flowControl='Software', pen_change='auto'), **extra)

        def idle():
            return status()['state'] == 'idle' and not status()['plot']['running']

        def last_plot():
            return status()['last_plot']

        # --- a whole plot through a real port -------------------------------------------------
        code, text = call('/start_plot', form())
        check('a plot starts', code == 200, text)
        check('it finishes', wait(idle, 60) and last_plot()['status'] == 'completed', last_plot())
        sent = sim.plot_bytes()
        check('the plotter received the whole file, in order', data in sent, '{} bytes received'.format(len(sent)))
        check('the plotter buffer never overflowed', 'OVERFLOW' not in sim.text())

        # --- pen change pause with buffer feedback ----------------------------------------------
        code, text = call('/start_plot', form(pen_change='pause'))
        check('a plot with pen change pauses starts', code == 200, text)
        waited = wait(lambda: status()['state'] == 'pen_change', 40)
        failed = last_plot()['status'] == 'failed' if not waited else False
        check('it waits for the pen change (and does not fail)', waited, last_plot() if failed else status()['state'])
        if waited:
            check('resume continues it', call('/resume_plot', {})[0] == 200)
            check('it finishes after the pen change', wait(idle, 60) and last_plot()['status'] == 'completed', last_plot())
        else:
            wait(idle, 30)

        # --- stop, then resume from the history ---------------------------------------------------
        before = len(sim.plot_bytes())
        code, text = call('/start_plot', form())
        wait(lambda: status()['plot']['progress'] >= 30, 40)
        call('/stop_plot', {})
        check('a stopped plot ends', wait(idle, 30) and last_plot()['status'] == 'stopped', last_plot())
        check('the plotter was told to abort', 'ABORT' in sim.text())
        rows = json.loads(call('/job_history')[1])
        stopped = rows[0]
        check('the stopped plot can be resumed', stopped.get('can_resume') is True, stopped)
        if stopped.get('can_resume'):
            mark = len(sim.plot_bytes())
            code, text = call('/resume_job', {'job': stopped['id'], 'rewind': '0'})
            check('resume starts', code == 200, text)
            check('the resumed plot finishes', wait(idle, 60) and last_plot()['status'] == 'completed', last_plot())
            tail = sim.plot_bytes()[mark:]
            check('the resumed data starts with the plotter set up again', tail.startswith(b'IN;IN;') or b'IN;' in tail[:12], tail[:40])
            check('and ends with the end of the file', tail.endswith(data[-200:]))
            gap = stopped['resume_offset']
            check('and picks up at the point the plotter had reached', data[gap:gap + 40] in tail, 'offset {}'.format(gap))

        # --- the cable comes out in the middle of a plot ------------------------------------------
        mark = len(sim.plot_bytes())
        code, text = call('/start_plot', form())
        wait(lambda: status()['plot']['progress'] >= 25, 40)
        sim.signal(signal.SIGUSR1)                                   # unplug
        check('an unplugged plotter holds the plot', wait(lambda: status()['state'] == 'disconnected', 20), status()['state'])
        check('resume is refused while it is unplugged', call('/resume_plot', {})[0] == 409)
        time.sleep(1.5)
        sim.signal(signal.SIGUSR2)                                   # plug it back in
        check('the plot notices the plotter is back', wait(lambda: status()['state'] == 'reconnect', 30), status()['state'])
        check('resume carries on', call('/resume_plot', {})[0] == 200)
        check('and finishes', wait(idle, 60) and last_plot()['status'] == 'completed', last_plot())
        tail = sim.plot_bytes()[mark:]
        check('the whole drawing reached the plotter across the unplug', tail.count(data[-100:]) >= 1)

        # --- a queue of two --------------------------------------------------------------------------
        with open(os.path.join(app_dir, 'uploads', 'sim2.hpgl'), 'wb') as f:
            f.write(drawing(pens=1, loops=10))
        call('/queue/add', form())
        call('/queue/add', form(file='sim2.hpgl'))
        check('the queue runs both files', call('/queue/start', {})[0] == 200 and wait(lambda: status()['queue']['waiting'] == 0 and idle(), 90))
        check('and reports it finished', json.loads(call('/queue')[1])['message'] == 'The queue is finished')
    finally:
        if sim:
            sim.stop()
        server.send_signal(signal.SIGTERM)
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()
        server_log.close()
        if failures:
            print('\nlogs kept in', folder)
        else:
            shutil.rmtree(folder, ignore_errors=True)

    print('\n{} check(s) failed'.format(len(failures)) if failures else '\nall checks passed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
