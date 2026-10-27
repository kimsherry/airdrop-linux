# SPDX-License-Identifier: MIT
"""The beacon's payload and the object BlueZ reads it from."""
import shutil
import subprocess
import unittest

import beacon


class PayloadTest(unittest.TestCase):
    def test_shape(self):
        data = beacon.payload(bytes(range(8)))
        self.assertEqual(len(data), 20)
        self.assertEqual(data[0], beacon.TYPE_AIRDROP)
        self.assertEqual(data[1], beacon.LENGTH)
        self.assertEqual(data[2:10], bytes(8))          # no identity
        self.assertEqual(data[10], beacon.VERSION)
        self.assertEqual(data[11:19], bytes(range(8)))  # the contact hashes
        self.assertEqual(data[19], 0)

    def test_random_hashes_differ(self):
        self.assertNotEqual(beacon.payload(), beacon.payload())

    def test_eight_bytes_of_hashes(self):
        with self.assertRaises(ValueError):
            beacon.payload(b'x')


@unittest.skipUnless(shutil.which('dbus-daemon'), 'needs dbus-daemon')
class AdvertisementTest(unittest.TestCase):
    def test_bluez_reads_the_company_id_and_bytes(self):
        import dbus
        import dbus.bus
        import dbus.mainloop
        import dbus.service

        daemon = subprocess.Popen(
            ['dbus-daemon', '--session', '--nofork', '--print-address=1'],
            stdout=subprocess.PIPE, text=True)
        try:
            bus = dbus.bus.BusConnection(daemon.stdout.readline().strip(),
                                         mainloop=dbus.mainloop.NULL_MAIN_LOOP)
            advertisement = beacon.advertisement_class(dbus)(bus, beacon.payload(bytes(8)))
            self.assertTrue(advertisement.locations)
            properties = advertisement.properties()
            self.assertEqual(properties['Type'], 'peripheral')
            data = properties['ManufacturerData'][dbus.UInt16(beacon.COMPANY_ID)]
            self.assertEqual(bytes(data), beacon.payload(bytes(8)))
            advertisement.remove_from_connection()
            bus.close()
        finally:
            daemon.stdout.close()
            daemon.terminate()
            daemon.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
