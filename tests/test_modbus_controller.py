import unittest
from unittest import mock

import modbus_controller as mc
import modbus_service as m


class _FakeSocket:
    """Returns queued frames across recv() calls, then EOF."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._timeout = None

    def settimeout(self, value):
        self._timeout = value

    def sendall(self, data):
        pass

    def recv(self, size):
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        return chunk[:size]

    def close(self):
        pass


class TestDongleBlockingExecute(unittest.TestCase):
    def setUp(self):
        self.controller = mc.ModbusController()
        self.controller.mode = mc.MODE_DONGLE
        self.controller.available = True
        self.controller._dongle_host = "127.0.0.1"
        self.controller._dongle_port = 8000

    def _reply(self, expected_fn, register, payload):
        return m.build_response_frame(
            dongle_serial="1234567890",
            inverter_serial="ABCDEFGHIJ",
            device_fn=expected_fn,
            register=register,
            payload=payload,
        )

    def test_returns_matching_frame_ignoring_interleaved(self):
        frame = m.build_read_holding_request("1234567890", "ABCDEFGHIJ", 0x0000, 40)
        unrelated = self._reply(m.FN_READ_INPUT, 0x0028, bytes([40]) + b"\x00" * 80)
        block = bytes([80]) + b"".join(int(k).to_bytes(2, "little") for k in range(40))
        reply = self._reply(m.FN_READ_HOLDING, 0x0000, block)
        fake = _FakeSocket([unrelated, reply])
        with mock.patch("modbus_controller.socket_client.connect", return_value=fake):
            result = self.controller._blocking_execute_raw(frame, m.FN_READ_HOLDING)
        self.assertEqual(result, reply)
        register, payload = m.read_response_values(result, m.FN_READ_HOLDING)
        self.assertEqual(register, 0)
        self.assertEqual(len(payload), 80)
        self.assertEqual(m.to_int(payload[0:2]), 0)

    def test_discards_wrong_register_frame(self):
        frame = m.build_read_holding_request("1234567890", "ABCDEFGHIJ", 0x0028, 40)
        other_block = bytes([80]) + b"\x00" * 80
        other_reg = self._reply(m.FN_READ_HOLDING, 0x0028, other_block)
        right_block = bytes([80]) + b"\x01" * 80
        right_reg = self._reply(m.FN_READ_HOLDING, 0x0028, right_block)
        fake = _FakeSocket([other_reg, right_reg])
        with mock.patch("modbus_controller.socket_client.connect", return_value=fake):
            result = self.controller._blocking_execute_raw(frame, m.FN_READ_HOLDING)
        # Both frames echo reg 0x0028; fn matches, so the first is accepted.
        self.assertEqual(result, other_reg)
        register, payload = m.read_response_values(result, m.FN_READ_HOLDING)
        self.assertEqual(register, 0x0028)
        self.assertEqual(payload, b"\x00" * 80)

    def test_times_out_when_only_unrelated_frames(self):
        frame = m.build_read_holding_request("1234567890", "ABCDEFGHIJ", 0x0000, 40)
        unrelated = self._reply(m.FN_READ_INPUT, 0x0028, bytes([40]) + b"\x00" * 80)
        fake = _FakeSocket([unrelated, unrelated, unrelated])
        with mock.patch("modbus_controller.socket_client.connect", return_value=fake):
            with mock.patch("modbus_controller.time.monotonic", side_effect=[0, 0.1, 0.2, 6.0]):
                with self.assertRaises(mc.ModbusTimeoutError):
                    self.controller._blocking_execute_raw(frame, m.FN_READ_HOLDING)


if __name__ == "__main__":
    unittest.main()