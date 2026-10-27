# SPDX-License-Identifier: MIT
"""The DNS messages the announcer builds and the queries it recognises."""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mdns  # noqa: E402

SERVICE = mdns.Service('_airdrop._tcp.local', '0a1b2c3d4e5f', '0a1b2c3d4e5f.local',
                       'fe80::5000:5eff:fe00:5301', 8771, 'flags=1019')


def parse_records(packet):
    """(name, type, class, ttl, rdata) for each answer of a response."""
    _, flags, questions, answers, _, _ = struct.unpack_from('!HHHHHH', packet)
    assert flags == mdns.RESPONSE_FLAGS and questions == 0
    records = []
    offset = 12
    for _ in range(answers):
        name, offset = mdns.decode_name(packet, offset)
        rtype, rclass, ttl, length = struct.unpack_from('!HHIH', packet, offset)
        offset += 10
        records.append((name, rtype, rclass, ttl, packet[offset:offset + length]))
        offset += length
    assert offset == len(packet)
    return records


def query(*names):
    packet = struct.pack('!HHHHHH', 0, 0, len(names), 0, 0, 0)
    for name in names:
        packet += mdns.encode_name(name) + struct.pack('!HH', mdns.TYPE_PTR, mdns.CLASS_IN)
    return packet


class ResponseTest(unittest.TestCase):
    def test_announcement_carries_the_whole_service(self):
        records = {(name, rtype): (rclass, ttl, rdata) for name, rtype, rclass, ttl, rdata
                   in parse_records(SERVICE.response())}
        instance = '0a1b2c3d4e5f._airdrop._tcp.local'
        host = '0a1b2c3d4e5f.local'

        pointer = records[('_airdrop._tcp.local', mdns.TYPE_PTR)]
        self.assertEqual(pointer[:2], (mdns.CLASS_IN, mdns.TTL_SERVICE))
        self.assertEqual(mdns.decode_name(pointer[2], 0)[0], instance)

        unique = mdns.CLASS_IN | mdns.CACHE_FLUSH
        server = records[(instance, mdns.TYPE_SRV)]
        self.assertEqual(server[:2], (unique, mdns.TTL_HOST))
        self.assertEqual(struct.unpack_from('!HHH', server[2]), (0, 0, 8771))
        self.assertEqual(mdns.decode_name(server[2], 6)[0], host)

        self.assertEqual(records[(instance, mdns.TYPE_TXT)][2], b'\x0aflags=1019')
        self.assertEqual(records[(host, mdns.TYPE_AAAA)][2], SERVICE.address.packed)
        self.assertIn((host, mdns.TYPE_NSEC), records)

    def test_goodbye_zeroes_every_lifetime(self):
        ttls = {ttl for _, _, _, ttl, _ in parse_records(SERVICE.response(alive=False))}
        self.assertEqual(ttls, {0})


class QueryTest(unittest.TestCase):
    def test_names_are_compared_without_case(self):
        asked = mdns.question_names(query('_AirDrop._tcp.LOCAL', '_ipp._tcp.local'))
        self.assertEqual(asked & SERVICE.names(), {'_airdrop._tcp.local'})

    def test_compressed_names_are_followed(self):
        packet = query('_airdrop._tcp.local')
        packet = packet[:4] + struct.pack('!H', 2) + packet[6:]
        instance = b'\x0c0a1b2c3d4e5f' + bytes([0xC0, 12])
        packet += instance + struct.pack('!HH', mdns.TYPE_SRV, mdns.CLASS_IN)
        self.assertEqual(mdns.question_names(packet),
                         {'_airdrop._tcp.local', '0a1b2c3d4e5f._airdrop._tcp.local'})

    def test_responses_and_garbage_ask_nothing(self):
        self.assertEqual(mdns.question_names(SERVICE.response()), set())
        self.assertEqual(mdns.question_names(b'\x00\x01'), set())
        looping = struct.pack('!HHHHHH', 0, 0, 1, 0, 0, 0) + bytes([0xC0, 12])
        self.assertEqual(mdns.question_names(looping), set())


if __name__ == '__main__':
    unittest.main()
