"""The spacenavd client: message parsing, requests, the handshake and the
connection, against a fake spacenavd on a Unix socket."""

import os
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import spacenav_client as proto  # noqa: E402
from fake_spacenavd import FakeSpacenavd  # noqa: E402


class ParsingTest(unittest.TestCase):
    def test_events_and_responses_interleaved_and_split(self):
        stream = (
            proto.pack_event(proto.UEV_RAWAXIS, 3, -120)
            + proto.pack_response(proto.REQ_SET_EVMASK, [0x30])
            + proto.pack_event(proto.UEV_RAWBUTTON, 22, 1)
            + proto.pack_event(proto.UEV_MOTION, 1, 2, 3, 4, 5, 6, 16)
        )
        parser = proto.StreamParser()
        items = []
        for cut in range(0, len(stream), 7):  # arbitrary pieces
            items.extend(parser.feed(stream[cut:cut + 7]))
        self.assertEqual(items[0], proto.SpnavEvent(proto.UEV_RAWAXIS, (3, -120, 0, 0, 0, 0, 0)))
        self.assertEqual(items[1].type, proto.REQ_TAG | proto.REQ_SET_EVMASK)
        self.assertEqual(items[1].data[0], 0x30)
        self.assertEqual(items[2], proto.SpnavEvent(proto.UEV_RAWBUTTON, (22, 1, 0, 0, 0, 0, 0)))
        self.assertEqual(items[3].data, (1, 2, 3, 4, 5, 6, 16))
        self.assertEqual(len(items), 4)
        self.assertEqual(parser.buffer, b"")

    def test_unknown_event_types_are_dropped(self):
        parser = proto.StreamParser()
        items = parser.feed(proto.pack_event(99, 1) + proto.pack_event(proto.UEV_PRESS, 4))
        self.assertEqual([item.type for item in items], [proto.UEV_PRESS])

    def test_messages_are_32_bytes(self):
        self.assertEqual(len(proto.pack_request(proto.REQ_SET_EVMASK, [0x30])), 32)
        self.assertEqual(len(proto.pack_event(proto.UEV_PRESS, 1)), 32)
        self.assertEqual(len(proto.pack_response(proto.REQ_GCFG_SENS)), 32)
        request = struct.unpack("=8i", proto.pack_request(proto.REQ_SET_EVMASK, [0x30]))
        self.assertEqual(request[0], 0x7FAA1003)
        self.assertEqual(request[1], 0x30)

    def test_strings_travel_in_chunks(self):
        for text in ("", "Turnpad", "x" * 24, "3Dconnexion SpacePilot Pro, a very long device name"):
            with self.subTest(length=len(text)):
                wire = proto.pack_string_request(proto.REQ_DEV_NAME, text)
                assembler = proto.StringAssembler()
                result = None
                for offset in range(0, len(wire), 32):
                    values = struct.unpack("=8i", wire[offset:offset + 32])
                    result = assembler.feed(proto.Response(values[0], tuple(values[1:])))
                self.assertEqual(result, text)

    def test_refused_string(self):
        with self.assertRaises(ValueError):
            proto.StringAssembler().feed(proto.Response(0, (0, 0, 0, 0, 0, 0, -1)))

    def test_float_bits(self):
        for value in (0.0, 1.0, 0.25, 2.5):
            self.assertEqual(proto.bits_to_float(proto.float_to_bits(value)), value)

    def test_socket_search_order(self):
        environ = {"SPNAV_SOCKET": "/env.sock", "XDG_RUNTIME_DIR": "/run/user/1000"}
        self.assertEqual(
            proto.socket_candidates("/mine.sock", environ, "# comment\nsocket = /etc.sock\n"),
            ["/mine.sock", "/env.sock", "/etc.sock", "/run/user/1000/spnav.sock", "/var/run/spnav.sock"],
        )
        self.assertEqual(proto.socket_candidates("", {}, ""), ["/var/run/spnav.sock"])
        self.assertIsNone(proto.spnavrc_socket("# socket = /x\nsensitivity = 1\n"))
        self.assertEqual(proto.spnavrc_socket("  socket=/y  \n"), "/y")


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.servers = []

    def patch_retry(self):
        """Waiting for an old daemon's answer twice takes seconds: shorter here."""
        original = proto.HANDSHAKE_TIMEOUT, proto.HANDSHAKE_RETRY_TIMEOUT
        proto.HANDSHAKE_TIMEOUT = proto.HANDSHAKE_RETRY_TIMEOUT = 0.05
        self.addCleanup(lambda: (setattr(proto, "HANDSHAKE_TIMEOUT", original[0]), setattr(proto, "HANDSHAKE_RETRY_TIMEOUT", original[1])))

    def server(self, **kwargs):
        server = FakeSpacenavd(self.directory.name, **kwargs)
        self.servers.append(server)
        self.addCleanup(server.stop)
        return server

    def connect(self, server):
        client = proto.SpacenavClient()
        self.addCleanup(client.close)
        self.assertTrue(client.connect(["/nonexistent/spnav.sock", server.path]))
        return client

    def test_v1_handshake_and_requests(self):
        server = self.server()
        client = self.connect(server)
        self.assertEqual(client.proto, 1)
        self.assertTrue(client.set_evmask(proto.EVMASK_RAWAXIS | proto.EVMASK_RAWBUTTON))
        self.assertEqual(server.evmask, 0x30)
        self.assertEqual(client.device_name(), "3Dconnexion  SpacePilot PRO ")
        self.assertEqual(client.get_sensitivity(), 1.0)
        self.assertTrue(client.set_sensitivity(0.0))
        self.assertEqual(server.sensitivity, 0.0)
        self.assertEqual(client.get_sensitivity(), 0.0)
        self.assertTrue(client.set_button_map(5, 63))
        self.assertEqual(client.get_button_map(5), 63)
        client.send_name("Turnpad")
        self.assertTrue(server.wait_for(lambda: server.client_name == "Turnpad"))

    def test_events_during_a_request_are_kept(self):
        server = self.server()
        client = self.connect(server)
        server.send(proto.pack_event(proto.UEV_RAWAXIS, 3, 100) + proto.pack_event(proto.UEV_RAWBUTTON, 0, 1))
        self.assertEqual(client.get_sensitivity(), 1.0)  # events arrive before/around the response
        server.send(proto.pack_event(proto.UEV_RAWBUTTON, 0, 0))
        server.wait_for(lambda: False, 0.05)
        events = client.read_events()
        self.assertEqual([(event.type, event.data[:2]) for event in events], [
            (proto.UEV_RAWAXIS, (3, 100)), (proto.UEV_RAWBUTTON, (0, 1)), (proto.UEV_RAWBUTTON, (0, 0)),
        ])
        self.assertEqual(client.read_events(), [])

    def test_refused_request_and_missing_device_name(self):
        server = self.server(device_name="")
        client = self.connect(server)
        self.assertIsNone(client.device_name())
        self.assertIsNone(client.request(0x3FFF))  # an unknown request is refused

    def test_old_daemon_falls_back_to_v0(self):
        self.patch_retry()
        server = self.server(version=None)
        client = self.connect(server)
        self.assertEqual(client.proto, 0)
        # An old daemon took the handshake for a sensitivity: it is put back to 1.0.
        self.assertTrue(server.wait_for(lambda: 1.0 in server.floats[1:]))
        self.assertIsNone(client.get_sensitivity())  # no requests in v0
        self.assertFalse(client.set_evmask(0x30))

    def test_a_late_v1_reply_does_not_desync_the_stream(self):
        # The reply comes after the first wait: that connection is dropped and
        # the second one, with a longer wait, gets the reply in time. A stray
        # reply must never end up in the middle of the 32-byte messages.
        original = proto.HANDSHAKE_TIMEOUT, proto.HANDSHAKE_RETRY_TIMEOUT
        proto.HANDSHAKE_TIMEOUT, proto.HANDSHAKE_RETRY_TIMEOUT = 0.05, 1.0
        self.addCleanup(lambda: (setattr(proto, "HANDSHAKE_TIMEOUT", original[0]), setattr(proto, "HANDSHAKE_RETRY_TIMEOUT", original[1])))
        server = self.server(first_reply_delay=0.3)
        client = self.connect(server)
        self.assertEqual(client.proto, 1)
        self.assertEqual(server.connections, 2)
        server.send(proto.pack_event(proto.UEV_RAWBUTTON, 3, 1))
        self.assertEqual(client.get_sensitivity(), 1.0)
        server.wait_for(lambda: False, 0.05)
        self.assertEqual([event.data[:2] for event in client.read_events()], [(3, 1)])

    def test_no_daemon(self):
        client = proto.SpacenavClient()
        self.assertFalse(client.connect([os.path.join(self.directory.name, "nothing.sock")]))
        self.assertFalse(client.connected)

    def test_daemon_goes_away(self):
        server = self.server()
        client = self.connect(server)
        server.drop_client()
        server.wait_for(lambda: False, 0.05)
        with self.assertRaises(OSError):
            client.read_events()

    def test_reconnect_after_the_daemon_is_back(self):
        server = self.server()
        client = self.connect(server)
        server.drop_client()
        client.close()
        self.assertTrue(client.connect([server.path]))
        self.assertEqual(server.connections, 2)
        self.assertEqual(client.proto, 1)

    def test_restore_file(self):
        restore = proto.RestoreFile(os.path.join(self.directory.name, "sub", "restore.json"))
        self.assertIsNone(restore.load())
        restore.save({"sensitivity": 0.5, "bnmap": {"3": 3}})
        self.assertEqual(restore.load(), {"sensitivity": 0.5, "bnmap": {"3": 3}})
        restore.clear()
        self.assertIsNone(restore.load())
        restore.clear()  # nothing to clear: no error


if __name__ == "__main__":
    unittest.main()
