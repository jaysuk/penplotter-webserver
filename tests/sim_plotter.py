#!/usr/bin/env python3
"""A simulated HP-GL plotter on a pseudo-terminal, for trying the real serial code without hardware.

    python3 tests/sim_plotter.py --rate 3000 --log /tmp/sim.log

It prints the port to use (/dev/pts/N). It answers the queries the web plotter makes (ESC.L buffer
size, ESC.B free space, ESC.E error, OI; and OA;), takes plot data into a buffer that drains at
--rate bytes a second, and empties the buffer on ESC.K (abort). Linux and macOS only.

    kill -USR1 <pid>   unplug: the port vanishes, writes to it fail
    kill -USR2 <pid>   plug it back in under the same name
    kill -TERM <pid>   stop

Everything received is appended to --log: the commands it saw, and every plot byte (so a test can
compare what was sent with the file). It does not model what a plotter draws, and it has no CTS
line: use Software (buffer based) or XON/XOFF flow control against it, not CTS/RTS.
"""
import argparse
import os
import pty
import re
import signal
import sys
import threading
import time
import tty

ESC = 0x1b


class Plotter:
    def __init__(self, rate, size, log_path):
        self.rate, self.size = rate, size
        self.used = 0.0
        self.stamp = time.time()
        self.lock = threading.Lock()
        self.log = open(log_path, 'a', buffering=1) if log_path else None
        self.master = self.slave = None
        self.name = None
        self.running = True
        self.data = bytearray()          # plot bytes received
        self.pending = b''

    def note(self, text):
        if self.log:
            self.log.write('{:.3f} {}\n'.format(time.time(), text))

    def drain(self):
        now = time.time()
        self.used = max(0.0, self.used - (now - self.stamp) * self.rate)
        self.stamp = now

    def plug(self, want=None):
        """Open a pseudo-terminal. With `want`, keep trying until it gets that name (a re-plug)."""
        spare = []
        for _ in range(200):
            master, slave = pty.openpty()
            name = os.ttyname(slave)
            if want is None or name == want:
                tty.setraw(slave)
                tty.setraw(master)
                self.master, self.slave, self.name = master, slave, name
                break
            spare.append((master, slave))
        for master, slave in spare:
            os.close(master)
            os.close(slave)
        if self.master is None:
            raise RuntimeError('could not get the port {} back'.format(want))
        self.note('PLUG {}'.format(self.name))

    def unplug(self):
        with self.lock:
            for fd in (self.master, self.slave):
                if fd is not None:
                    os.close(fd)
            self.master = self.slave = None
            self.pending = b''
        self.note('UNPLUG')

    def reply(self, text):
        try:
            os.write(self.master, text.encode() + b'\r')
        except OSError:
            pass

    def feed(self, chunk):
        """Handle bytes from the web plotter."""
        data = self.pending + chunk
        self.pending = b''
        i = 0
        plot = bytearray()
        while i < len(data):
            byte = data[i]
            if byte == ESC:
                if i + 2 >= len(data):
                    self.pending = data[i:]
                    break
                kind = chr(data[i + 2])
                self.drain()
                if data[i + 1:i + 2] != b'.':
                    self.note('BAD ESC {!r}'.format(data[i:i + 3]))
                    i += 2
                    continue
                if kind == 'L':
                    self.reply(str(self.size))
                    self.note('ESC.L')
                elif kind == 'B':
                    self.reply(str(int(max(self.size - self.used, 0))))
                elif kind == 'E':
                    self.reply('0')
                elif kind == 'K':
                    self.used = 0.0
                    self.note('ABORT (ESC.K)')
                else:
                    self.note('ESC.{}'.format(kind))
                i += 3
                continue
            text = data[i:i + 3]
            if text == b'OI;':
                self.reply('7475A')
                self.note('OI;')
                i += 3
                continue
            if text == b'OA;':
                self.reply('4019,-2009,1')
                self.note('OA;')
                i += 3
                continue
            plot.append(byte)
            i += 1
        if plot:
            self.drain()
            self.used += len(plot)
            self.data += plot
            if self.log:
                self.log.write('{:.3f} DATA {}\n'.format(time.time(), bytes(plot).decode('latin-1').replace('\n', '\\n')))
            if self.used > self.size:
                self.note('OVERFLOW: {} bytes in a {} byte buffer'.format(int(self.used), self.size))

    def serve(self):
        while self.running:
            with self.lock:
                master = self.master
            if master is None:
                time.sleep(0.05)
                continue
            try:
                import select
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    chunk = os.read(master, 4096)
                    if chunk:
                        self.feed(chunk)
            except OSError:
                time.sleep(0.05)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--rate', type=float, default=3000, help='bytes the plotter works through per second')
    parser.add_argument('--buffer', type=int, default=1024, help='buffer size in bytes')
    parser.add_argument('--log', default=None, help='file to log to')
    parser.add_argument('--pidfile', default=None)
    args = parser.parse_args()

    plotter = Plotter(args.rate, args.buffer, args.log)
    plotter.plug()
    name = plotter.name
    print(name, flush=True)
    if args.pidfile:
        with open(args.pidfile, 'w') as f:
            f.write(str(os.getpid()))

    signal.signal(signal.SIGUSR1, lambda *_: plotter.unplug())
    signal.signal(signal.SIGUSR2, lambda *_: plotter.plug(name) if plotter.master is None else None)
    signal.signal(signal.SIGTERM, lambda *_: setattr(plotter, 'running', False))
    try:
        plotter.serve()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
