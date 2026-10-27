# SPDX-License-Identifier: MIT
"""Decoding and storing uploads, with archives built here in memory."""
import gzip
import io
import os
import socket
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cpio  # noqa: E402
import transfer  # noqa: E402

REGULAR = 0o100644
DIRECTORY = 0o040755


def portable_member(name, data=b'', mode=REGULAR):
    raw = name.encode() + b'\0'
    header = '070707' + '000000' * 2 + f'{mode:06o}' + '000000' * 4
    header += f'{0:011o}{len(raw):06o}{len(data):011o}'
    return header.encode() + raw + data


def new_ascii_member(name, data=b'', mode=REGULAR):
    raw = name.encode() + b'\0'
    fields = [0, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(raw), 0]
    out = b'070701' + ''.join(f'{field:08x}' for field in fields).encode() + raw
    out += b'\0' * (-len(out) % 4)
    return out + data + b'\0' * (-len(data) % 4)


def archive(*members, member=portable_member):
    return b''.join(members) + member(cpio.TRAILER, mode=0)


def padded(raw):
    """Fill the archive's last record with zeros, as senders do."""
    return raw + b'\0' * (-len(raw) % cpio.RECORD_BYTES)


def dvzip(*blocks):
    """Blocks are (stored, payload) pairs in DVZip's length-prefixed framing."""
    out = b''
    for stored, payload in blocks:
        body = payload if stored else zlib.compress(payload)
        out += (len(body) | (0x80000000 if stored else 0)).to_bytes(4, 'big') + body
    return out


class Budget:
    percent = 30
    free_bytes = 64 * transfer.MIB

    def __init__(self, byte_limit=16 * transfer.MIB):
        self.byte_limit = byte_limit


class CpioTest(unittest.TestCase):
    def names_and_data(self, raw):
        return [(entry.pathname, entry.isfile, b''.join(entry.blocks()))
                for entry in cpio.read_entries(io.BytesIO(raw))]

    def test_portable_format(self):
        raw = archive(portable_member('.', mode=DIRECTORY),
                      portable_member('./a.txt', b'alpha'),
                      portable_member('./b.bin', b'\0\1\2'))
        self.assertEqual(self.names_and_data(raw),
                         [('.', False, b''), ('./a.txt', True, b'alpha'),
                          ('./b.bin', True, b'\0\1\2')])

    def test_new_ascii_format_is_padded_to_four_bytes(self):
        raw = archive(new_ascii_member('odd', b'12345'),
                      new_ascii_member('next', b'xy'), member=new_ascii_member)
        self.assertEqual(self.names_and_data(raw),
                         [('odd', True, b'12345'), ('next', True, b'xy')])

    def test_unread_data_is_skipped(self):
        raw = archive(portable_member('first', b'skipped'), portable_member('second', b'kept'))
        names = [entry.pathname for entry in cpio.read_entries(io.BytesIO(raw))]
        self.assertEqual(names, ['first', 'second'])

    def test_bad_magic_and_truncation_are_errors(self):
        with self.assertRaises(cpio.CpioError):
            self.names_and_data(b'\xc7\x71' + b'\0' * 100)
        with self.assertRaises(cpio.CpioError):
            self.names_and_data(portable_member('cut', b'12345')[:-2])


class ArchiveEndTest(unittest.TestCase):
    RAW = padded(archive(portable_member('.', mode=DIRECTORY),
                         portable_member('./clip.mov', bytes(range(256)) * 9),
                         portable_member('./still.jpg', b'jpeg')))

    def end_position(self, body, step):
        """How many bytes of `body`, fed `step` at a time, it takes before
        the watcher reports a complete archive; None if it never does."""
        watcher = transfer.ArchiveEndWatcher()
        for start in range(0, len(body), step):
            if watcher.feed(body[start:start + step]):
                return start + step
        return None

    def test_scanner_wants_the_trailer_and_a_full_last_record(self):
        scanner = cpio.TrailerScanner()
        for start in range(0, len(self.RAW) - 1, 7):
            scanner.feed(self.RAW[start:min(start + 7, len(self.RAW) - 1)])
            self.assertFalse(scanner.complete)
        scanner.feed(self.RAW[-1:])
        self.assertTrue(scanner.complete)

    def test_gzip_body_ends_once_its_data_is_out(self):
        body = gzip.compress(self.RAW)
        # Only gzip's own checksum and length may still be on their way.
        self.assertLessEqual(len(body) - self.end_position(body, 1), 12)
        self.assertEqual(self.end_position(body, 4096), 4096)

    def test_dvzip_body_ends_with_its_last_block(self):
        third = len(self.RAW) // 3
        body = dvzip((True, self.RAW[:third]), (False, self.RAW[third:2 * third]),
                     (True, self.RAW[2 * third:]))
        self.assertEqual(self.end_position(body, 1), len(body))
        self.assertEqual(self.end_position(body, 5), len(body) + (-len(body) % 5))

    def test_a_cut_or_foreign_body_never_ends(self):
        body = dvzip((True, self.RAW))
        self.assertIsNone(self.end_position(body[:-1], 3))
        self.assertIsNone(self.end_position(b'not an archive at all' * 40, 3))

    def test_watching_file_reports_once_and_writes_everything(self):
        body = gzip.compress(self.RAW)
        calls = []
        target = io.BytesIO()
        watched = transfer.EndWatchingFile(target, lambda: calls.append(1))
        for start in range(0, len(body), 50):
            watched.write(body[start:start + 50])
        watched.write(b'trailing bytes')
        self.assertEqual(calls, [1])
        self.assertEqual(target.getvalue(), body + b'trailing bytes')


class SafeNameTest(unittest.TestCase):
    def test_only_the_last_component_survives(self):
        self.assertEqual(transfer.safe_name('../../etc/passwd', 'file'), 'passwd')
        self.assertEqual(transfer.safe_name('C:\\Windows\\evil.dll', 'file'), 'evil.dll')

    def test_windows_specific_tricks_are_defused(self):
        self.assertEqual(transfer.safe_name('photo.jpg:hidden', 'file'), 'photo.jpghidden')
        self.assertEqual(transfer.safe_name('CON.txt', 'file'), '_CON.txt')
        self.assertEqual(transfer.safe_name('nul', 'file'), '_nul')
        self.assertEqual(transfer.safe_name('report.pdf. .', 'file'), 'report.pdf')

    def test_hidden_and_empty_names_fall_back(self):
        self.assertEqual(transfer.safe_name('.hidden', 'file'), 'hidden')
        self.assertEqual(transfer.safe_name('...', 'file'), 'file')
        self.assertEqual(transfer.safe_name('gpj.\u202eexe', 'file'), 'gpj.exe')


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dest = self.temp.name
        self.addCleanup(self.temp.cleanup)

    def store(self, body, budget=None):
        with tempfile.TemporaryFile(dir=self.dest) as raw:
            raw.write(body)
            return transfer.store_upload(raw, self.dest, 'test', budget or Budget())

    def read(self, name):
        with open(os.path.join(self.dest, name), 'rb') as stored:
            return stored.read()

    def test_gzip_body_is_extracted(self):
        payload = bytes(range(256)) * 300
        raw = archive(portable_member('.', mode=DIRECTORY),
                      portable_member('./photo.jpg', payload),
                      portable_member('./._photo.jpg', b'finder metadata'))
        self.assertEqual(self.store(gzip.compress(raw)), ['photo.jpg'])
        self.assertEqual(self.read('photo.jpg'), payload)

    def test_metadata_companion_of_a_name_with_backslashes_is_skipped(self):
        name = 'C:\\Users\\someone\\clip.MP4'
        raw = archive(portable_member('./' + name, b'video'),
                      portable_member('./._' + name, b'AppleDouble'))
        self.assertEqual(self.store(gzip.compress(raw)), ['clip.MP4'])

    def test_dvzip_blocks_are_joined(self):
        raw = archive(portable_member('./note.txt', b'line one\r\nline two\n'))
        middle = len(raw) // 2
        body = dvzip((False, raw[:middle]), (True, raw[middle:]))
        self.assertEqual(self.store(body), ['note.txt'])
        self.assertEqual(self.read('note.txt'), b'line one\r\nline two\n')

    def live_photo(self, stem):
        return gzip.compress(archive(
            portable_member('.', mode=DIRECTORY),
            portable_member(f'./{stem}.pvt', mode=DIRECTORY),
            portable_member(f'./{stem}.pvt/{stem}.MOV', b'video'),
            portable_member(f'./{stem}.pvt/{stem}.jpg', b'still'),
            portable_member(f'./{stem}.pvt/metadata.plist', b'<plist/>')))

    def test_a_live_photo_is_stored_as_its_still_and_its_video(self):
        self.assertEqual(self.store(self.live_photo('IMG_1')), ['IMG_1.MOV', 'IMG_1.jpg'])
        self.assertEqual(sorted(os.listdir(self.dest)), ['IMG_1.MOV', 'IMG_1.jpg'])

    def test_a_live_photo_keeps_one_name_for_both_files(self):
        with open(os.path.join(self.dest, 'img_1.JPG'), 'wb') as earlier:
            earlier.write(b'the plain photo sent before')
        self.assertEqual(self.store(self.live_photo('IMG_1')), ['IMG_1-1.MOV', 'IMG_1-1.jpg'])
        self.assertEqual(self.store(self.live_photo('IMG_1')), ['IMG_1-2.MOV', 'IMG_1-2.jpg'])
        self.assertEqual(self.read('IMG_1-2.jpg'), b'still')

    def test_a_live_photo_says_which_two_files_it_is(self):
        written = self.store(self.live_photo('IMG_1'))
        self.assertEqual(transfer.live_photos(written), [['IMG_1.jpg', 'IMG_1.MOV']])

    def test_a_photo_and_a_video_of_one_name_are_not_a_live_photo(self):
        raw = archive(portable_member('./IMG_1.jpg', b'still'),
                      portable_member('./IMG_1.MOV', b'video'))
        self.assertEqual(transfer.live_photos(self.store(gzip.compress(raw))), [])

    def test_a_metadata_file_outside_a_live_photo_is_kept(self):
        raw = archive(portable_member('./metadata.plist', b'ordinary'))
        self.assertEqual(self.store(gzip.compress(raw)), ['metadata.plist'])

    def test_an_existing_file_is_never_overwritten(self):
        with open(os.path.join(self.dest, 'a.txt'), 'wb') as existing:
            existing.write(b'mine')
        raw = archive(portable_member('./a.txt', b'theirs'))
        self.assertEqual(self.store(gzip.compress(raw)), ['a-1.txt'])
        self.assertEqual(self.read('a.txt'), b'mine')

    def test_a_failed_upload_leaves_nothing_behind(self):
        raw = archive(portable_member('./kept.txt', b'ok'))[:-40]
        with self.assertRaises(ValueError):
            self.store(gzip.compress(raw))
        self.assertEqual(os.listdir(self.dest), [])

    def test_output_beyond_the_budget_is_refused(self):
        raw = archive(portable_member('./big.bin', b'x' * 5000))
        with self.assertRaises(transfer.UploadLimitError):
            self.store(gzip.compress(raw), Budget(byte_limit=4096))
        self.assertEqual(os.listdir(self.dest), [])

    @unittest.skipUnless(os.name == 'nt', 'zone marks exist on NTFS only')
    def test_stored_files_carry_the_internet_zone_mark(self):
        self.store(gzip.compress(archive(portable_member('./tool.exe', b'MZ'))))
        self.assertIn(b'ZoneId=3', self.read('tool.exe:Zone.Identifier'))

    def test_links_are_stored_inert(self):
        name = transfer.store_link('https://example.org/some/page', self.dest)
        self.assertEqual(name, 'example.org-page.url')
        self.assertEqual(self.read(name),
                         b'[InternetShortcut]\r\nURL=https://example.org/some/page\r\n')

    def test_only_plain_web_links_qualify(self):
        self.assertTrue(transfer.is_web_link('https://example.org/a?b=c'))
        self.assertFalse(transfer.is_web_link('file:///C:/Windows/win.ini'))
        self.assertFalse(transfer.is_web_link('https://example.org/\r\nIconFile=\\\\host\\share'))
        self.assertFalse(transfer.is_web_link(b'https://example.org/'))


class BodyTest(unittest.TestCase):
    def test_chunked_body_is_reassembled(self):
        wire = io.BytesIO(b'5\r\nhello\r\n6; ext=1\r\n world\r\n0\r\n\r\n')
        body = transfer.read_small_body(wire, {'Transfer-Encoding': 'chunked'})
        self.assertEqual(body, b'hello world')

    def test_oversized_bodies_are_refused_before_reading(self):
        with self.assertRaises(transfer.BodyTooLarge):
            transfer.read_small_body(io.BytesIO(b''), {'Content-Length': '2000000'})
        wire = io.BytesIO(b'20\r\n' + b'x' * 32 + b'\r\n0\r\n\r\n')
        with self.assertRaises(transfer.BodyTooLarge):
            transfer.read_small_body(wire, {'Transfer-Encoding': 'chunked'}, limit=16)

    def test_a_stall_reports_where_the_body_stopped(self):
        class Stalling(io.BytesIO):
            def read1(self, size=-1):
                data = super().read1(size)
                if not data:
                    raise TimeoutError()
                return data

        deadline = transfer.ReadDeadline()
        wire = Stalling(b'5\r\nhello\r\n8\r\nwor')
        with self.assertRaises(socket.timeout) as stalled:
            transfer.read_request_body(wire, {'Transfer-Encoding': 'chunked'},
                                       io.BytesIO(), 100, deadline=deadline)
        report = deadline.stall_report(stalled.exception, None)
        self.assertIn('16 bytes', report)
        self.assertIn('waiting for the last 5 of 8 bytes after 1 chunks', report)

    def test_missing_framing_is_refused(self):
        with self.assertRaises(transfer.RequestBodyError):
            transfer.read_small_body(io.BytesIO(b'abc'), {})


if __name__ == '__main__':
    unittest.main()
