# SPDX-License-Identifier: MIT
"""The engine against a sender, against another engine, and as the process a
program drives, all over loopback. Needs OpenSSL, like the sender's tests."""
import argparse
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import cpio  # noqa: E402
import engine  # noqa: E402
import mdns  # noqa: E402
import receiver  # noqa: E402
import sender  # noqa: E402
import transfer  # noqa: E402
from test_sender import RefusingReceiver  # noqa: E402

LOOPBACK_SCOPE = 0
WAIT_SECONDS = 15
SERVICE_TYPE = '_airdrop._tcp.local'


class Heard:
    """Stands in for the driving program: keeps an engine's events and waits
    for one of them."""

    def __init__(self):
        self.events = []
        self.closed = threading.Event()
        self._changed = threading.Condition()
        self._taken = set()

    def say(self, event, **fields):
        with self._changed:
            self.events.append({'event': event, **fields})
            self._changed.notify_all()

    def wait(self, event, **fields):
        """The first event of this kind and with these fields that no earlier
        wait returned."""
        wanted = {'event': event, **fields}
        deadline = time.monotonic() + WAIT_SECONDS
        with self._changed:
            while True:
                for index, said in enumerate(self.events):
                    if index not in self._taken and wanted.items() <= said.items():
                        self._taken.add(index)
                        return said
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f'no {wanted} among {self.events}')
                self._changed.wait(left)

    def kinds(self, event, key):
        with self._changed:
            return [said[key] for said in self.events if said['event'] == event]


class EngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.keys.cleanup)
        cls.client_context = sender.client_context(cls.keys.name)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def engine(self, *extra, name='Receiver PC'):
        folder = tempfile.mkdtemp(dir=self.temp.name)
        args = engine.parse_args(['--loopback', '--port', '0', '--keys', self.keys.name,
                                  '--name', name, '--outdir', folder, *extra])
        heard = Heard()
        running = engine.Engine(args, heard)
        running.start()
        self.addCleanup(running.stop)
        heard.wait('receiving', on=True)
        return running, heard, folder

    def source_file(self, name, data):
        path = os.path.join(self.temp.name, name)
        with open(path, 'wb') as out:
            out.write(data)
        return path

    def deliver(self, port, files, outcome):
        outcome.append(sender.deliver('::1', port, LOOPBACK_SCOPE, self.client_context,
                                      'Sender PC', files))

    def start_delivery(self, port, *paths):
        outcome = []
        thread = threading.Thread(target=self.deliver,
                                  args=(port, sender.outgoing(list(paths)), outcome))
        thread.start()
        self.addCleanup(thread.join)
        return thread, outcome

    def test_an_accepted_ask_is_stored_and_reported(self):
        running, heard, folder = self.engine()
        data = os.urandom(5 * sender.IO_BLOCK_BYTES + 3)
        thread, outcome = self.start_delivery(running.settings.port,
                                              self.source_file('clip.mp4', data))
        asked = heard.wait('asked')
        self.assertEqual((asked['sender'], asked['waits'], asked['total']),
                         ('Sender PC', True, len(data)))
        self.assertEqual(asked['files'],
                         [{'name': 'clip.mp4', 'size': len(data), 'type': 'public.mpeg-4'}])
        running.handle({'do': 'answer', 'id': asked['id'], 'accept': True})
        self.assertEqual(heard.wait('answered', id=asked['id'])['how'], 'accepted')
        stored = heard.wait('stored', id=asked['id'])
        thread.join()
        self.assertEqual(outcome, [sender.SENT])
        # The engine gives the folder's full name; a temporary one may come in its short form.
        self.assertTrue(os.path.samefile(stored['folder'], folder))
        self.assertEqual(stored['names'], ['clip.mp4'])
        with open(os.path.join(folder, 'clip.mp4'), 'rb') as source:
            self.assertEqual(source.read(), data)
        done = [said['done'] for said in heard.events if said['event'] == 'incoming']
        self.assertEqual(done[0], 0)
        self.assertEqual(done, sorted(done))
        self.assertLessEqual(done[-1], len(data))

    def test_an_answer_given_the_moment_it_is_asked_counts(self):
        running, heard, _ = self.engine()
        said = heard.say

        def answer_at_once(event, **fields):
            said(event, **fields)
            if event == 'asked':
                running.handle({'do': 'answer', 'id': fields['id'], 'accept': True})

        heard.say = answer_at_once
        thread, outcome = self.start_delivery(running.settings.port,
                                              self.source_file('note.txt', b'now'))
        self.assertEqual(heard.wait('stored')['names'], ['note.txt'])
        thread.join()
        self.assertEqual(outcome, [sender.SENT])

    def test_a_declined_ask_stores_nothing(self):
        running, heard, folder = self.engine()
        thread, outcome = self.start_delivery(running.settings.port,
                                              self.source_file('note.txt', b'hello'))
        asked = heard.wait('asked')
        running.handle({'do': 'answer', 'id': asked['id'], 'accept': False})
        thread.join()
        self.assertEqual(outcome, [sender.DECLINED])
        self.assertEqual(heard.wait('answered', id=asked['id'])['how'], 'declined')
        self.assertEqual(os.listdir(folder), [])

    def test_a_sender_that_stops_waiting_closes_its_ask(self):
        running, heard, _ = self.engine()
        connection = sender.Connection('::1', running.settings.port, LOOPBACK_SCOPE,
                                       self.client_context, WAIT_SECONDS)
        connection.connect()
        ask = sender.ask_body('Sender PC', [sender.Outgoing('x', 'a.txt', 1, 0)], 'AN-ID')
        connection.request('POST', '/Ask', body=plistlib.dumps(ask, fmt=plistlib.FMT_BINARY),
                           headers={'Content-Type': 'application/octet-stream'})
        asked = heard.wait('asked')
        connection.close()
        self.assertEqual(heard.wait('answered', id=asked['id'])['how'], 'left')
        self.assertFalse(running.incoming.answer(asked['id'], True))

    def test_an_upload_nobody_was_asked_about_is_refused(self):
        running, heard, folder = self.engine()
        files = sender.outgoing([self.source_file('note.txt', b'hello')])
        body = b''.join(sender.archive_body(files, sender.Progress(5)))
        connection = sender.Connection('::1', running.settings.port, LOOPBACK_SCOPE,
                                       self.client_context, WAIT_SECONDS)
        self.addCleanup(connection.close)
        connection.request('POST', '/Upload', body=body,
                           headers={'Content-Type': 'application/x-dvzip', 'TransferID': 'X'})
        self.assertEqual(connection.getresponse().status, 401)
        self.assertEqual(os.listdir(folder), [])
        self.assertEqual(heard.kinds('asked', 'id'), [])

    def test_taking_everything_asks_nobody(self):
        running, heard, folder = self.engine('--take-all')
        thread, outcome = self.start_delivery(running.settings.port,
                                              self.source_file('note.txt', b'hello'))
        thread.join()
        self.assertEqual(outcome, [sender.SENT])
        self.assertFalse(heard.wait('asked')['waits'])
        self.assertEqual(heard.wait('stored')['names'], ['note.txt'])
        self.assertEqual(os.listdir(folder), ['note.txt'])

    def test_a_changed_name_is_what_the_next_sender_gets(self):
        running, heard, _ = self.engine()
        running.handle({'do': 'set', 'name': 'Study', 'folder': 'Z:\\no\\such\\folder',
                        'ask': False})
        said = heard.wait('settings')
        self.assertEqual((said['name'], said['ask']), ('Study', False))
        self.assertEqual(list(said['refused']), ['folder'])
        self.assertEqual(
            sender.receiver_identity('::1', running.settings.port, LOOPBACK_SCOPE,
                                     self.client_context),
            ('Study', 'MacBookPro18,3'))

    def pair(self):
        receiving, heard_there, folder = self.engine(name='Receiver PC')
        sending, heard_here, _ = self.engine(
            '--peer', f'[::1]:{receiving.settings.port}', name='Sender PC')
        sending.handle({'do': 'look', 'on': True})
        peer = heard_here.wait('peer')
        self.assertEqual((peer['name'], peer['model']), ('Receiver PC', 'MacBookPro18,3'))
        return receiving, heard_there, folder, sending, heard_here, peer['id']

    def test_one_engine_sends_to_another(self):
        receiving, heard_there, folder, sending, heard_here, peer = self.pair()
        data = os.urandom(40 * sender.IO_BLOCK_BYTES)
        paths = [self.source_file('clip.mp4', data), self.source_file('note.txt', b'hello')]
        sending.handle({'do': 'send', 'id': 's1', 'peer': peer, 'files': paths})
        asked = heard_there.wait('asked')
        self.assertEqual(asked['sender'], 'Sender PC')
        self.assertEqual(heard_here.wait('send', id='s1')['state'], 'asking')
        receiving.handle({'do': 'answer', 'id': asked['id'], 'accept': True})
        sent = heard_here.wait('send', id='s1', state='sent')
        self.assertEqual(sent['done'], sent['total'])
        self.assertEqual(sorted(heard_there.wait('stored')['names']), ['clip.mp4', 'note.txt'])
        with open(os.path.join(folder, 'clip.mp4'), 'rb') as source:
            self.assertEqual(source.read(), data)
        states = heard_here.kinds('send', 'state')
        self.assertEqual([state for index, state in enumerate(states)
                          if index == 0 or states[index - 1] != state],
                         ['asking', 'sending', 'finishing', 'sent'])
        self.assertEqual(sent['total'], cpio.archive_length(
            [('.', 0), ('./clip.mp4', len(data)), ('./note.txt', 5)]))

    def test_a_cancelled_send_is_seen_to_leave(self):
        receiving, heard_there, _, sending, heard_here, peer = self.pair()
        sending.handle({'do': 'send', 'id': 's1', 'peer': peer,
                        'files': [self.source_file('note.txt', b'hello')]})
        asked = heard_there.wait('asked')
        sending.handle({'do': 'cancel', 'id': 's1'})
        heard_here.wait('send', id='s1', state='cancelled')
        self.assertEqual(heard_there.wait('answered', id=asked['id'])['how'], 'left')

    def test_what_cannot_be_sent_says_why(self):
        _, _, _, sending, heard_here, peer = self.pair()
        sending.handle({'do': 'send', 'id': 's1', 'peer': peer, 'files': [self.temp.name]})
        self.assertEqual(heard_here.wait('send', id='s1')['reason'], 'not_a_file')
        sending.handle({'do': 'send', 'id': 's2', 'peer': 'nobody', 'files': []})
        self.assertEqual(heard_here.wait('send', id='s2')['reason'], 'gone')

    def test_a_receiver_that_refuses_this_computer_is_counted_and_not_shown(self):
        with tempfile.TemporaryDirectory() as stranger:
            refusing = RefusingReceiver(self.keys.name, stranger)
            self.addCleanup(refusing.close)
            running, heard, _ = self.engine('--peer', f'[::1]:{refusing.port}')
            running.handle({'do': 'look', 'on': True})
            self.assertEqual(heard.wait('unwilling')['count'], 1)
            self.assertEqual(heard.kinds('peer', 'id'), [])
            running.handle({'do': 'look', 'on': False})
            heard.wait('unwilling', count=0)

    def test_a_receiver_that_gives_no_name_is_counted_too(self):
        settings = argparse.Namespace(
            name='', model='MacBookPro18,3', dest=self.temp.name, max_receive_percent=30,
            interface_index=LOOPBACK_SCOPE, redact=False)
        nameless = receiver.Receiver(0, settings)
        nameless.socket = receiver.tls_context(self.keys.name).wrap_socket(
            nameless.socket, server_side=True, do_handshake_on_connect=False)
        thread = threading.Thread(target=nameless.serve_forever, daemon=True)
        thread.start()

        def stop():
            nameless.shutdown()
            thread.join()
            nameless.server_close()

        self.addCleanup(stop)
        running, heard, _ = self.engine('--peer', f'[::1]:{nameless.server_address[1]}')
        running.handle({'do': 'look', 'on': True})
        self.assertEqual(heard.wait('unwilling')['count'], 1)
        self.assertEqual(heard.kinds('peer', 'id'), [])

    def test_looking_stops_when_told(self):
        _, _, _, sending, heard_here, peer = self.pair()
        sending.handle({'do': 'look', 'on': False})
        self.assertEqual(heard_here.wait('peer_gone')['id'], peer)
        heard_here.wait('looking', on=False)


class ProcessTest(unittest.TestCase):
    def test_it_is_driven_by_lines_and_ends_with_its_input(self):
        with tempfile.TemporaryDirectory() as keys, tempfile.TemporaryDirectory() as folder:
            # Creates the certificate here, so the process has it to load.
            sender.client_context(keys)
            process = subprocess.Popen(
                [sys.executable, os.path.join(os.path.dirname(HERE), 'engine.py'),
                 '--loopback', '--port', '0', '--keys', keys, '--outdir', folder,
                 '--name', 'Nom à accents'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                encoding='utf-8')
            self.addCleanup(process.kill)

            def said(event):
                while True:
                    line = process.stdout.readline()
                    self.assertTrue(line, f'the engine ended before saying {event}')
                    heard = json.loads(line)
                    if heard['event'] == event:
                        return heard

            ready = said('ready')
            self.assertEqual((ready['protocol'], ready['name'], ready['ask']),
                             (engine.PROTOCOL, 'Nom à accents', True))
            self.assertTrue(said('receiving')['on'])
            process.stdin.write('not json\n{"do": "set", "name": "Autre nom"}\n')
            process.stdin.flush()
            self.assertIn('JSON', said('error')['reason'])
            self.assertEqual(said('settings')['name'], 'Autre nom')
            process.stdin.close()
            self.assertEqual(process.wait(WAIT_SECONDS), 0)
            process.stdout.close()


class PiecesTest(unittest.TestCase):
    def test_an_archive_is_as_long_as_said(self):
        members = [('.', cpio.DIRECTORY_MODE, 0, 0, []),
                   ('./a.txt', cpio.FILE_MODE, 5, 1700000000, [b'hello']),
                   ('./photo.jpg', cpio.FILE_MODE, 700, 1700000000, [b'x' * 700])]
        written = sum(len(piece) for piece in cpio.write_archive(members))
        self.assertEqual(
            cpio.archive_length([(name, size) for name, _, size, _, _ in members]), written)

    def test_an_instance_not_announced_lately_is_left_out(self):
        instance = f'0a1b2c3d4e5f.{SERVICE_TYPE}'
        service = mdns.Service(SERVICE_TYPE, '0a1b2c3d4e5f', '0a1b2c3d4e5f.local',
                               'fe80::5000:5eff:fe00:5301', 8770, 'flags=1019')
        instances = mdns.Instances(SERVICE_TYPE)
        instances.feed(service.response(), 'fe80::1')
        self.assertEqual(list(instances.located(within=5)), [instance])
        instances._heard[instance] -= 6
        self.assertEqual(instances.located(within=5), {})
        self.assertEqual(list(instances.located()), [instance])
        instances.feed(service.response(), 'fe80::1')
        self.assertEqual(list(instances.located(within=5)), [instance])

    def test_a_device_announced_under_two_names_is_one_device(self):
        address = 'fe80::5000:5eff:fe00:5301'
        instances = mdns.Instances(SERVICE_TYPE)
        for label, port in (('0a1b2c3d4e5f', 8770), ('f5e4d3c2b1a0', 8771)):
            service = mdns.Service(SERVICE_TYPE, label, f'{label}.local', address, port,
                                   'flags=1019')
            instances.feed(service.response(), 'fe80::1')
        instances._heard[f'0a1b2c3d4e5f.{SERVICE_TYPE}'] -= 1
        self.assertEqual(len(instances.located()), 2)
        self.assertEqual(instances.devices(), {address: (address, 8771)})

    def test_what_was_stored_says_which_files_are_one_live_photo(self):
        heard = Heard()
        incoming = engine.Incoming(heard, argparse.Namespace(dest='folder'))
        names = [transfer.Stored('IMG_1.MOV', 'IMG_1.pvt'),
                 transfer.Stored('IMG_1.jpg', 'IMG_1.pvt'), transfer.Stored('note.txt')]
        incoming.stored(engine.Accepted('r1', None), names)
        stored = heard.wait('stored')
        self.assertEqual(stored['names'], ['IMG_1.MOV', 'IMG_1.jpg', 'note.txt'])
        self.assertEqual(stored['live'], [['IMG_1.jpg', 'IMG_1.MOV']])


if __name__ == '__main__':
    unittest.main()
