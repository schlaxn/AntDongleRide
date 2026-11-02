import queue
import threading
import time
import unittest
from collections import deque

from rower.serialdev import RowerError, RowerSerial, parse_payload, printable, split_lines


class FakeSerial:
    """Stands in for pyserial: read() returns queued chunks, write() records bytes."""

    def __init__(self, chunks=()):
        self._chunks = deque(chunks)
        self.written = []
        self.closed = False

    def read(self, n):
        if self._chunks:
            return self._chunks.popleft()
        time.sleep(0.005)
        return b""

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def close(self):
        self.closed = True


def wait_for(predicate, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class SplitLinesTest(unittest.TestCase):
    def test_splits_crlf_and_lf_and_keeps_partial_tail(self):
        buf = bytearray(b"_WR_\r\nSTROKE 1\nPART")
        self.assertEqual(split_lines(buf), ["_WR_", "STROKE 1"])
        self.assertEqual(bytes(buf), b"PART")

    def test_partial_line_is_completed_by_next_chunk(self):
        buf = bytearray(b"AB")
        self.assertEqual(split_lines(buf), [])
        buf.extend(b"C\r\n")
        self.assertEqual(split_lines(buf), ["ABC"])
        self.assertEqual(bytes(buf), b"")


class PrintableTest(unittest.TestCase):
    def test_escapes_control_bytes(self):
        self.assertEqual(printable(b"A\r\n\x00\xff~"), "A\\r\\n\\x00\\xff~")


class ParsePayloadTest(unittest.TestCase):
    def test_ascii_escapes(self):
        self.assertEqual(parse_payload("USB\\r\\n", "ascii"), b"USB\r\n")

    def test_hex(self):
        self.assertEqual(parse_payload("55 53 42 0d 0a", "hex"), b"USB\r\n")

    def test_bad_hex_raises(self):
        with self.assertRaises(ValueError):
            parse_payload("zz", "hex")

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            parse_payload("x", "binary")


class RowerSerialTest(unittest.TestCase):
    def test_receive_publishes_raw_chunk_and_lines(self):
        fake = FakeSerial([b"_WR_\r\nSTR", b"OKE\r\n"])
        rower = RowerSerial(opener=lambda port, baud: fake)
        q = rower.subscribe()
        rower.connect("COM9", 115200)
        try:
            rx = []
            end = time.time() + 2
            while len(rx) < 2 and time.time() < end:
                try:
                    item = q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if item.get("dir") == "RX":
                    rx.append(item)
            self.assertEqual(len(rx), 2)
            self.assertEqual(rx[0]["lines"], ["_WR_"])
            self.assertEqual(rx[1]["lines"], ["STROKE"])
            self.assertEqual(rx[0]["raw_hex"], "5f 57 52 5f 0d 0a 53 54 52")
            self.assertEqual(rower.rx_bytes, len(b"_WR_\r\nSTR") + len(b"OKE\r\n"))
        finally:
            rower.disconnect()
            rower.unsubscribe(q)
        self.assertTrue(fake.closed)

    def test_send_writes_bytes_and_logs_tx(self):
        fake = FakeSerial()
        rower = RowerSerial(opener=lambda port, baud: fake)
        rower.connect("COM9", 9600)
        try:
            rower.send(b"USB\r\n")
            self.assertEqual(fake.written, [b"USB\r\n"])
            tx = [e for e in rower.events if e["dir"] == "TX"]
            self.assertEqual(len(tx), 1)
            self.assertEqual(tx[0]["text"], "USB\\r\\n")
            self.assertEqual(rower.tx_bytes, 5)
        finally:
            rower.disconnect()

    def test_send_when_disconnected_raises(self):
        rower = RowerSerial(opener=lambda port, baud: FakeSerial())
        with self.assertRaises(RowerError):
            rower.send(b"x")

    def test_double_connect_raises(self):
        rower = RowerSerial(opener=lambda port, baud: FakeSerial())
        rower.connect("COM9", 115200)
        try:
            with self.assertRaises(RowerError):
                rower.connect("COM10", 115200)
        finally:
            rower.disconnect()

    def test_slow_subscriber_drops_instead_of_blocking(self):
        rower = RowerSerial(opener=lambda port, baud: FakeSerial())
        q = rower.subscribe()
        done = threading.Event()

        def flood():
            for i in range(2500):
                rower._publish({"n": i})
            done.set()

        t = threading.Thread(target=flood)
        t.start()
        t.join(timeout=2)
        self.assertTrue(done.is_set(), "publish blocked on a full queue")
        self.assertEqual(q.qsize(), 2000)
        rower.unsubscribe(q)

    def test_events_are_kept_for_export(self):
        rower = RowerSerial(opener=lambda port, baud: FakeSerial())
        rower.connect("COM9", 115200)
        rower.disconnect()
        texts = [e["text"] for e in rower.events if e["dir"] == "EVT"]
        self.assertTrue(any("connected to COM9" in t for t in texts))
        self.assertTrue(any("disconnected" in t for t in texts))


if __name__ == "__main__":
    unittest.main()
