import struct
import unittest

from ant.protocol import FrameParser, describe, encode


class EncodeTests(unittest.TestCase):
    def test_reset_vector(self):
        # ResetSystem: A4 01 4A 00 EF  (checksum = XOR of the first four bytes)
        self.assertEqual(encode(0x4A, b"\x00"), bytes.fromhex("A4 01 4A 00 EF"))

    def test_open_channel_vector(self):
        self.assertEqual(encode(0x4B, b"\x00"), bytes.fromhex("A4 01 4B 00 EE"))

    def test_empty_payload(self):
        self.assertEqual(encode(0x54), bytes.fromhex("A4 00 54 F0"))


class ParserTests(unittest.TestCase):
    def test_single_frame(self):
        p = FrameParser()
        frames = p.feed(bytes.fromhex("A4 01 6F 00 CA"), ts=1.0)  # startup message
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].msg_id, 0x6F)
        self.assertEqual(frames[0].data, b"\x00")

    def test_split_across_chunks(self):
        p = FrameParser()
        raw = encode(0x4E, bytes(range(9)))
        self.assertEqual(p.feed(raw[:4]), [])
        frames = p.feed(raw[4:])
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].data, bytes(range(9)))

    def test_two_frames_in_one_chunk(self):
        p = FrameParser()
        frames = p.feed(encode(0x4A, b"\x00") + encode(0x6F, b"\x00"))
        self.assertEqual([f.msg_id for f in frames], [0x4A, 0x6F])

    def test_garbage_before_frame_and_bad_checksum(self):
        p = FrameParser()
        bad = bytearray(encode(0x4A, b"\x00"))
        bad[-1] ^= 0xFF
        frames = p.feed(b"\x12\x34" + bytes(bad) + encode(0x6F, b"\x01"))
        self.assertEqual([f.msg_id for f in frames], [0x6F])
        self.assertGreaterEqual(p.bad_checksums, 1)


class DescribeTests(unittest.TestCase):
    def test_event(self):
        self.assertIn("RX_FAIL", describe(0x40, bytes([0, 0x01, 0x02])))

    def test_broadcast_with_extended_device_id(self):
        payload = bytes(8)
        ext = bytes([0x80]) + struct.pack("<H", 1234) + bytes([120, 0])
        s = describe(0x4E, bytes([0]) + payload + ext)
        self.assertIn("devnum=1234", s)
        self.assertIn("devtype=120", s)


if __name__ == "__main__":
    unittest.main()
