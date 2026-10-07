"""Runs a conversion (vpype) in a child process, so it does not share the web server's interpreter.

vpype is CPU heavy. In the server's own process it takes turns with the thread that streams a plot to
the serial port (the GIL), which showed as longer gaps between writes and a slower Stop. In a child
process, started at a low priority, the operating system keeps the sender running first.

No Flask and no vpype here: the child imports `convert_vpype` itself, and the parent only starts it.
The two talk through lines of JSON on the child's standard output, each starting with PREFIX (anything
else vpype prints is passed on to the server's console):

    parent -> child, on stdin: {"function": "convert_file", "args": [...], "kwargs": {...}}
    child -> parent: {"emit": [name, data]} (progress for the page) and finally
                     {"result": ...} or {"error": "...", "kind": "<exception class>"}

`python convert_runner.py` is the child.
"""
import json
import os
import shutil
import subprocess
import sys
import threading

PREFIX = '@@webplotter '
TIMEOUT = 30 * 60           # a conversion that takes longer than this is killed (a big file on a Pi Zero is slow)
FUNCTIONS = ('convert_file', 'create_text')

HERE = os.path.dirname(os.path.abspath(__file__))


class ConversionError(Exception):
    """The conversion did not finish. The message is for the user."""


_lock = threading.Lock()
_current = None             # the child that is running, and why it may have been killed
_killed_for = None


def child_command():
    return [sys.executable, os.path.join(HERE, 'convert_runner.py')]


def _low_priority(command):
    """Start the child at a low priority: `nice` where there is one (not Windows)."""
    nice = shutil.which('nice') if os.name != 'nt' else None
    return [nice, '-n', '10'] + command if nice else command


def _creation_flags():
    return getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0) if os.name == 'nt' else 0


def running():
    with _lock:
        return _current is not None


def _kill(process, reason):
    global _killed_for
    with _lock:
        if _current is process:
            _killed_for = reason
    try:
        process.kill()
    except OSError:
        pass


def cancel():
    """Stop the conversion that is running. Returns False when there is none."""
    with _lock:
        process = _current
    if process is None:
        return False
    _kill(process, 'cancelled')
    return True


def run(function, args, kwargs, emit=None, command=None, timeout=TIMEOUT, error_types=None, cwd=None):
    """Run `convert_vpype.<function>(*args, **kwargs)` in a child process and return its result.

    `emit(name, data)` gets what the function sends to its `socketio` (progress messages). `error_types`
    maps the name of an exception class of the child to the exception to raise here for it; any
    other failure is a ConversionError. One conversion at a time (the caller holds a lock)."""
    global _current, _killed_for
    if function not in FUNCTIONS:
        raise ValueError('Unknown conversion ' + str(function))
    command = list(command) if command else _low_priority(child_command())
    request = json.dumps({'function': function, 'args': list(args), 'kwargs': kwargs}).encode('utf-8')
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   cwd=cwd or HERE, creationflags=_creation_flags())
    except OSError as e:
        raise ConversionError('Could not start the conversion: ' + str(e))
    with _lock:
        _current = process
        _killed_for = None
    timer = threading.Timer(timeout, _kill, (process, 'timeout'))
    timer.daemon = True
    timer.start()
    outcome = None
    try:
        try:
            process.stdin.write(request)
            process.stdin.close()
        except OSError:
            pass            # the child ended at once: what it printed explains why
        for raw in process.stdout:
            line = raw.decode('utf-8', 'replace').rstrip('\r\n')
            if not line.startswith(PREFIX):
                if line.strip():
                    print('convert:', line)
                continue
            try:
                message = json.loads(line[len(PREFIX):])
            except ValueError:
                continue
            if 'emit' in message and emit is not None:
                name, data = message['emit']
                emit(name, data)
            elif 'result' in message or 'error' in message:
                outcome = message
        process.wait()
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
            process.wait()
        for stream in (process.stdout, process.stdin):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        with _lock:
            reason, _current = _killed_for, None

    if reason == 'cancelled':
        raise ConversionError('The conversion was cancelled')
    if reason == 'timeout':
        raise ConversionError('The conversion took longer than {} and was stopped'.format(
            '{} minutes'.format(round(timeout / 60)) if timeout >= 60 else '{} seconds'.format(round(timeout))))
    if outcome is None:
        raise ConversionError('The conversion stopped without an answer (exit code {}): the Pi may have run out '
                              'of memory. Try a smaller drawing, or turn off "Convert in a separate '
                              'process" in the settings.'.format(process.returncode))
    if 'error' in outcome:
        exception = (error_types or {}).get(outcome.get('kind'))
        if exception is not None:
            raise exception(outcome['error'])
        raise ConversionError(outcome['error'])
    return outcome['result']


# ---- the child ---------------------------------------------------------------------------------

class _Emitter:
    """Stands in for socketio in the child: what convert_file emits goes to the parent."""

    def __init__(self, out):
        self.out = out

    def emit(self, name, data=None, **kwargs):
        send(self.out, {'emit': [name, data]})


def send(out, message):
    out.write(PREFIX + json.dumps(message) + '\n')
    out.flush()


def child_main(stdin, stdout, functions=None):
    """Read one request, run it, write the answer. `functions` stands in for convert_vpype in tests."""
    request = json.loads(stdin.read())
    try:
        if request['function'] not in FUNCTIONS:
            raise ValueError('Unknown conversion')
        if functions is None:
            import convert_vpype
            functions = {name: getattr(convert_vpype, name) for name in FUNCTIONS}
        kwargs = dict(request.get('kwargs') or {})
        if request['function'] == 'convert_file':
            kwargs['socketio'] = _Emitter(stdout)
        result = functions[request['function']](*request.get('args', []), **kwargs)
        send(stdout, {'result': result})
    except (Exception, SystemExit) as e:
        send(stdout, {'error': str(e) or repr(e), 'kind': type(e).__name__})
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(child_main(sys.stdin, sys.stdout))
