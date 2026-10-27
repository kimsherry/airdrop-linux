# SPDX-License-Identifier: MIT
"""The Bluetooth advertisement an Apple device sends before an AirDrop, on
BlueZ. Devices nearby that are visible to everyone turn AWDL on for it.

It carries no identity: eight zero bytes, the version, and eight random bytes
that stand in for the hashes of the sender's contact details. The payload is
the one winawdl's Windows beacon sends (scripts/airdrop-beacon.ps1)."""
import logging
import secrets
import threading

COMPANY_ID = 0x004C
TYPE_AIRDROP = 0x05
LENGTH = 0x12
VERSION = 0x01
ADVERTISEMENT_PATH = '/org/airdrop/beacon'
ANSWER_SECONDS = 10

_ADVERTISEMENT = None


def payload(hashes=None):
    """The manufacturer data: type 5, 18 bytes, version 1, four two-byte
    hashes of contact details, then a zero."""
    hashes = secrets.token_bytes(8) if hashes is None else hashes
    if len(hashes) != 8:
        raise ValueError('the four contact hashes are eight bytes')
    return (bytes([TYPE_AIRDROP, LENGTH]) + bytes(8) + bytes([VERSION])
            + hashes + bytes(1))


def advertisement_class(dbus):
    """BlueZ reads these properties and calls Release when it stops."""
    class Advertisement(dbus.service.Object):
        def __init__(self, bus, data):
            super().__init__(bus, ADVERTISEMENT_PATH)
            self.data = data

        def properties(self):
            return {
                'Type': dbus.String('peripheral'),
                'ManufacturerData': dbus.Dictionary(
                    {dbus.UInt16(COMPANY_ID): dbus.Array(self.data, signature='y')},
                    signature='qv'),
            }

        @dbus.service.method('org.freedesktop.DBus.Properties',
                             in_signature='ss', out_signature='v')
        def Get(self, interface, name):
            return self.properties()[name]

        @dbus.service.method('org.freedesktop.DBus.Properties',
                             in_signature='s', out_signature='a{sv}')
        def GetAll(self, interface):
            return self.properties()

        @dbus.service.method('org.bluez.LEAdvertisement1', in_signature='',
                             out_signature='')
        def Release(self):
            pass

    return Advertisement


class Beacon:
    """Advertises until the context is left, and says why if it cannot.

    The caller carries on without a beacon: then only a receiver whose own
    share sheet is open announces itself over mDNS."""

    def __init__(self, data=None):
        self.data = payload() if data is None else data
        self.bus = None
        self.loop = None
        self.thread = None
        self.adapter = None
        self.object = None
        self.on = False
        self.said = ''

    def __enter__(self):
        try:
            self._start()
            self.on = True
            logging.info('Bluetooth beacon on (%s)', self.adapter)
        except Exception as error:  # dbus-python errors share no base class
            self.said = str(error) or type(error).__name__
            self._stop()
            logging.warning('no Bluetooth beacon (%s): a receiver shows up only '
                            'while its own share sheet is open', self.said)
        return self

    def _start(self):
        import dbus
        import dbus.mainloop.glib
        import dbus.service
        from gi.repository import GLib

        global _ADVERTISEMENT
        if _ADVERTISEMENT is None:
            _ADVERTISEMENT = advertisement_class(dbus)
        # BlueZ calls back into the advertisement object, so a loop has to be
        # dispatching before it is registered.
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        manager_path, self.adapter = self._find_adapter(dbus)
        self.object = _ADVERTISEMENT(self.bus, self.data)
        self.loop = GLib.MainLoop()
        self.thread = threading.Thread(target=self.loop.run, name='beacon', daemon=True)
        self.thread.start()
        manager = dbus.Interface(self.bus.get_object('org.bluez', manager_path),
                                 'org.bluez.LEAdvertisingManager1')
        self._call(manager.RegisterAdvertisement, dbus.ObjectPath(ADVERTISEMENT_PATH), {})

    def _call(self, method, *args):
        """A blocking method would wait while BlueZ waits for our loop thread,
        which can deadlock; this waits on the reply instead."""
        done = threading.Event()
        outcome = {}

        def answered(*_):
            done.set()

        def failed(error):
            outcome['error'] = error
            done.set()

        method(*args, reply_handler=answered, error_handler=failed, timeout=ANSWER_SECONDS)
        if not done.wait(ANSWER_SECONDS):
            raise RuntimeError('BlueZ did not answer')
        if 'error' in outcome:
            raise outcome['error']

    def _find_adapter(self, dbus):
        """The path of an adapter that is powered and can advertise."""
        manager = dbus.Interface(self.bus.get_object('org.bluez', '/'),
                                 'org.freedesktop.DBus.ObjectManager')
        for path, interfaces in manager.GetManagedObjects().items():
            if 'org.bluez.LEAdvertisingManager1' not in interfaces:
                continue
            if not interfaces.get('org.bluez.Adapter1', {}).get('Powered'):
                continue
            return str(path), str(path).rsplit('/', 1)[-1]
        raise RuntimeError('no powered Bluetooth adapter that can advertise')

    def __exit__(self, *_):
        self.on = False
        self._stop()

    def _stop(self):
        if self.bus is None:
            return
        if self.object is not None and self.adapter:
            import dbus
            try:
                manager = self.bus.get_object('org.bluez', f'/org/bluez/{self.adapter}')
                self._call(manager.UnregisterAdvertisement,
                           dbus.ObjectPath(ADVERTISEMENT_PATH))
            except Exception as error:
                logging.debug('beacon: unregister failed: %s', error)
        if self.object is not None:
            try:
                self.object.remove_from_connection()
            except Exception:
                pass
        if self.loop is not None:
            self.loop.quit()
        if self.thread is not None:
            self.thread.join(timeout=ANSWER_SECONDS)
        self.bus.close()
        self.bus = self.loop = self.thread = self.object = None
