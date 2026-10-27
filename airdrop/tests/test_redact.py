# SPDX-License-Identifier: MIT
"""What --redact leaves of the lines the receiver prints."""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import redact  # noqa: E402

WORDS = [('STUDY-PC', 'this-pc'), ('C:\\Users\\ada', '~'), ('ada', 'user')]


class RedactorTest(unittest.TestCase):
    def setUp(self):
        self.redactor = redact.Redactor(WORDS)

    def test_an_address_becomes_a_numbered_device(self):
        hide = self.redactor.hide
        self.assertEqual(hide('connection from [fe80::5000:5eff:fe00:5301%10]:57853'),
                         'connection from [device-1]:57853')
        self.assertEqual(hide('mdns: fe80::1 asked for _airdrop._tcp.local'),
                         'mdns: device-2 asked for _airdrop._tcp.local')
        self.assertEqual(hide('fe80::5000:5EFF:FE00:5301 "POST /Ask HTTP/1.1" 200 -'),
                         'device-1 "POST /Ask HTTP/1.1" 200 -')

    def test_a_mac_address_becomes_a_numbered_device(self):
        self.assertEqual(self.redactor.hide('peer 52:00:5e:00:53:01: gone'),
                         'peer device-1: gone')
        self.assertEqual(self.redactor.hide('peer 52:0:5e:0:53:1 back'), 'peer device-1 back')

    def test_a_named_address_keeps_its_name(self):
        self.redactor.name('fe80::5000:5eff:fe00:5301', 'own-address')
        self.assertEqual(self.redactor.hide('on [fe80::5000:5eff:fe00:5301%10]:8771'),
                         'on [own-address]:8771')

    def test_other_colons_are_left_alone(self):
        for line in ('2026-10-11 05:36:16,852 INFO upload 96756EF0-460F-42F8: 30% of 21 bytes',
                     'storage failed: [WinError 10054] reset', 'ratio 16:9, port :8771',
                     'D:\\Drops\\a.jpg'):
            self.assertEqual(self.redactor.hide(line), line)

    def test_the_computer_and_the_account_are_replaced(self):
        hide = self.redactor.hide
        self.assertEqual(hide('receiving as Study-PC into C:\\Users\\ada\\Downloads'),
                         'receiving as this-pc into ~\\Downloads')
        self.assertEqual(hide('File "c:\\users\\ADA\\winawdl\\airdrop\\receiver.py", line 3'),
                         'File "~\\winawdl\\airdrop\\receiver.py", line 3')
        self.assertEqual(hide('D:/home/ada/x and adagio'), 'D:/home/user/x and adagio')

    def test_no_words_is_allowed(self):
        self.assertEqual(redact.Redactor([('', 'x')]).hide('plain'), 'plain')


class StreamTest(unittest.TestCase):
    def test_lines_are_redacted_whole_even_when_written_in_pieces(self):
        shown = io.StringIO()
        stream = redact.Stream(shown, redact.Redactor(WORDS).hide)
        stream.write('from fe80::50')
        stream.flush()
        self.assertEqual(shown.getvalue(), '')
        stream.write('00:5eff:fe00:5301 on STUDY-PC\nnext ')
        self.assertEqual(shown.getvalue(), 'from device-1 on this-pc\n')
        stream.write('line\n')
        self.assertEqual(shown.getvalue(), 'from device-1 on this-pc\nnext line\n')

    def test_the_rest_of_the_stream_is_passed_through(self):
        shown = io.StringIO()
        self.assertEqual(redact.Stream(shown, str).writable(), shown.writable())


if __name__ == '__main__':
    unittest.main()
