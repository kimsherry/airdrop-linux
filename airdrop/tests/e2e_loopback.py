# SPDX-License-Identifier: MIT
"""End-to-end check of the AirDrop layer without a radio.

Two engines over loopback: one receives, one sends a generated file. Covers
the TLS handshake, /Discover, /Ask, /Upload, the archive framing and what
lands on disk. Run it after changing anything in this package:

    PYTHONPATH=. python3 tests/e2e_loopback.py
"""
import hashlib
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8781


def events(process):
    out = queue.Queue()

    def pump():
        for line in process.stdout:
            if line.strip():
                out.put(json.loads(line))
    threading.Thread(target=pump, daemon=True).start()
    return out


def wait_for(queue_, name, timeout, when=lambda event: True):
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        try:
            event = queue_.get(timeout=max(0.1, deadline - time.monotonic()))
        except queue.Empty:
            break
        seen.append(event)
        if event.get('event') == name and when(event):
            return event
    raise SystemExit(f'{name} never came; saw {seen[-6:]}')


def talk(work):
    source = os.path.join(work, 'hello.txt')
    with open(source, 'wb') as out:
        out.write(os.urandom(200000))

    receiver = subprocess.Popen(
        [sys.executable, 'engine.py', '--loopback', '--port', str(PORT),
         '--take-all', '--outdir', work, '--name', 'LinuxPC'],
        cwd=HERE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    recv = events(receiver)
    wait_for(recv, 'receiving', 20)

    sender = subprocess.Popen(
        [sys.executable, 'engine.py', '--loopback', '--peer', f'[::1]:{PORT}',
         '--port', str(PORT + 1), '--name', 'LinuxSender'],
        cwd=HERE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    send = events(sender)
    wait_for(send, 'link', 20)
    sender.stdin.write('{"do":"look","on":true}\n')
    sender.stdin.flush()
    wait_for(send, 'peer', 20)
    sender.stdin.write(json.dumps({'do': 'send', 'id': '1',
                                   'peer': f'[::1]:{PORT}.fixed',
                                   'files': [source]}) + '\n')
    sender.stdin.flush()
    outcome = wait_for(send, 'send', 40, lambda event: event['state'] in ('sent', 'failed'))
    stored = wait_for(recv, 'stored', 40)

    for process in (sender, receiver):
        process.stdin.write('{"do":"quit"}\n')
        process.stdin.flush()
        process.wait(timeout=20)

    assert outcome['state'] == 'sent', outcome
    assert stored['names'], stored
    digest = lambda path: hashlib.sha256(open(path, 'rb').read()).hexdigest()
    landed = os.path.join(work, stored['names'][0])
    assert digest(landed) == digest(source), landed
    return outcome, stored


def main():
    with tempfile.TemporaryDirectory() as work:
        outcome, stored = talk(work)
    print(json.dumps({'send': outcome['state'], 'stored': stored['names'],
                      'bytes': outcome['total']}))


if __name__ == '__main__':
    main()
