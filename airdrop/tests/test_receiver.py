# SPDX-License-Identifier: MIT
"""The HTTPS endpoints end to end over loopback, with a real TLS handshake but
no AWDL adapter. Needs OpenSSL to create the receiver's certificate."""
import argparse
import gzip
import http.client
import os
import plistlib
import ssl
import sys
import tempfile
import socket
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import receiver  # noqa: E402
import transfer  # noqa: E402
from test_transfer import archive, padded, portable_member  # noqa: E402

# Loopback connections carry no interface scope.
LOOPBACK_SCOPE = 0
OTHER_INTERFACE = 4242


class ReceiverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.keys.cleanup)
        cls.context = receiver.tls_context(cls.keys.name)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = argparse.Namespace(
            name='Test PC', model='MacBookPro18,3', dest=self.temp.name,
            max_receive_percent=30, interface_index=LOOPBACK_SCOPE, redact=False)
        self.server = receiver.Receiver(0, self.settings)
        self.server.socket = self.context.wrap_socket(
            self.server.socket, server_side=True, do_handshake_on_connect=False)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.stop_server, thread)

    def stop_server(self, thread):
        self.server.shutdown()
        thread.join()
        self.server.reset_sessions()
        self.server.server_close()

    def connect(self):
        trusting = ssl.create_default_context()
        trusting.check_hostname = False
        trusting.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection(
            '::1', self.server.server_address[1], context=trusting, timeout=10)
        self.addCleanup(connection.close)
        return connection

    def post_plist(self, connection, path, value):
        body = plistlib.dumps(value, fmt=plistlib.FMT_BINARY)
        connection.request('POST', path, body=iter([body]), encode_chunked=True,
                           headers={'Transfer-Encoding': 'chunked'})
        response = connection.getresponse()
        return response.status, response.read()

    def test_discover_names_this_receiver(self):
        status, body = self.post_plist(self.connect(), '/Discover', {})
        self.assertEqual(status, 200)
        answer = plistlib.loads(body)
        self.assertEqual(answer['ReceiverComputerName'], 'Test PC')
        self.assertEqual(answer['ReceiverModelName'], 'MacBookPro18,3')
        self.assertIn('ReceiverMediaCapabilities', answer)

    def test_ask_then_upload_on_one_connection_stores_the_file(self):
        connection = self.connect()
        ask = {'SenderComputerName': 'Phone', 'Files': [{'FileName': 'a.txt'}]}
        self.assertEqual(self.post_plist(connection, '/Ask', ask)[0], 200)

        payload = gzip.compress(archive(portable_member('./a.txt', b'sent over TLS')))
        connection.request('POST', '/Upload', body=payload,
                           headers={'Content-Type': 'application/x-dvzip'})
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 200)
        with open(os.path.join(self.temp.name, 'a.txt'), 'rb') as stored:
            self.assertEqual(stored.read(), b'sent over TLS')

    def upload_without_end_marker(self, payload):
        """Send every chunk of an upload but not the zero-length one that
        ends it, as an iPhone does for a video. Returns the status line."""
        trusting = ssl.create_default_context()
        trusting.check_hostname = False
        trusting.verify_mode = ssl.CERT_NONE
        plain = socket.create_connection(('::1', self.server.server_address[1]),
                                         timeout=10)
        with trusting.wrap_socket(plain) as tls:
            tls.sendall(b'POST /Upload HTTP/1.1\r\nHost: receiver\r\n'
                        b'Content-Type: application/x-dvzip\r\n'
                        b'Transfer-Encoding: chunked\r\n\r\n' +
                        b'%x\r\n' % len(payload) + payload + b'\r\n')
            return tls.recv(4096).split(b'\r\n', 1)[0]

    def test_a_complete_upload_is_answered_without_waiting_for_its_end(self):
        payload = gzip.compress(padded(archive(portable_member('./a.mov', b'live'))))
        with mock.patch.object(transfer, 'END_MARKER_WAIT_SECONDS', 0.2):
            status = self.upload_without_end_marker(payload)
        self.assertEqual(status, b'HTTP/1.1 200 OK')
        with open(os.path.join(self.temp.name, 'a.mov'), 'rb') as stored:
            self.assertEqual(stored.read(), b'live')

    def test_a_complete_upload_is_kept_when_its_end_marker_never_comes(self):
        payload = gzip.compress(archive(portable_member('./clip.mp4', b'video')))
        with mock.patch.object(transfer, 'READ_IDLE_TIMEOUT_SECONDS', 1):
            status = self.upload_without_end_marker(payload)
        self.assertEqual(status, b'HTTP/1.1 200 OK')
        with open(os.path.join(self.temp.name, 'clip.mp4'), 'rb') as stored:
            self.assertEqual(stored.read(), b'video')

    def test_a_cut_off_upload_is_still_refused(self):
        payload = gzip.compress(archive(portable_member('./clip.mp4', b'video')))
        with mock.patch.object(transfer, 'READ_IDLE_TIMEOUT_SECONDS', 1):
            status = self.upload_without_end_marker(payload[:-8])
        self.assertEqual(status, b'HTTP/1.1 408 Request Timeout')
        self.assertEqual(os.listdir(self.temp.name), [])

    def test_a_link_is_stored_by_the_ask_alone(self):
        ask = {'TransferType': {'links': True},
               'Items': ['https://example.org/page', 'file:///C:/Windows/win.ini']}
        self.assertEqual(self.post_plist(self.connect(), '/Ask', ask)[0], 200)
        self.assertEqual(os.listdir(self.temp.name), ['example.org-page.url'])

    def test_redacting_keeps_the_sender_and_its_folders_out_of_the_log(self):
        self.settings.redact = True
        ask = {'SenderComputerName': 'Ada phone', 'SenderModelName': 'iPhone',
               'Files': [{'FileName': 'C:\\Users\\ada\\trip/a.txt', 'FileSize': 3}]}
        with self.assertLogs(level='INFO') as logs:
            self.assertEqual(self.post_plist(self.connect(), '/Ask', ask)[0], 200)
        asked = next(line for line in logs.output if 'ask from' in line)
        self.assertIn('ask from a sender (iPhone)', asked)
        self.assertIn(' a.txt ', asked)
        self.assertNotIn('Ada', asked)
        self.assertNotIn('trip', asked)

    def test_an_upload_of_another_type_is_refused(self):
        connection = self.connect()
        connection.request('POST', '/Upload', body=b'plain',
                           headers={'Content-Type': 'text/plain'})
        self.assertEqual(connection.getresponse().status, 415)
        self.assertEqual(os.listdir(self.temp.name), [])

    def test_connections_from_other_interfaces_are_dropped(self):
        self.settings.interface_index = OTHER_INTERFACE
        with self.assertRaises(OSError):
            self.post_plist(self.connect(), '/Discover', {})


if __name__ == '__main__':
    unittest.main()
