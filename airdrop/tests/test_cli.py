# SPDX-License-Identifier: MIT
"""The command-line receiver asks before it stores anything."""
import contextlib
import io
import sys
import unittest
from unittest import mock

import receiver

ASK = {'SenderComputerName': 'Phone', 'SenderModelName': 'iPhone14,2',
       'Files': [{'FileName': 'a.jpg', 'FileSize': 12}]}


def asked(answer):
    out = io.StringIO()
    with mock.patch.object(sys, 'stdin', io.StringIO(answer)), \
            contextlib.redirect_stdout(out):
        result = receiver.Terminal().asked(ASK, '::1', lambda: False)
    return result, out.getvalue()


class TerminalTest(unittest.TestCase):
    def test_yes_accepts_and_names_the_sender(self):
        result, shown = asked('y\n')
        self.assertTrue(result)
        self.assertIn('Phone (iPhone14,2)', shown)
        self.assertIn('a.jpg (12B)', shown)

    def test_anything_else_declines(self):
        for answer in ('n\n', '\n', 'sure\n'):
            with self.subTest(answer=answer):
                self.assertIsNone(asked(answer)[0])

    def test_a_closed_input_declines_rather_than_blocks(self):
        self.assertIsNone(asked('')[0])

    def test_what_was_stored_is_said(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            receiver.Terminal().stored(None, ['a.jpg', 'b.jpg'])
        self.assertIn('a.jpg, b.jpg', out.getvalue())


if __name__ == '__main__':
    unittest.main()
