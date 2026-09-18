import asyncio
import unittest
from unittest import mock

import modbus_controller as mc
import modbus_registers as mr
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


class _FakeReadController(mc.ModbusController):
    """Records requested ranges and returns register==value payloads."""

    def __init__(self):
        super().__init__()
        self.mode = mc.MODE_DONGLE
        self.available = True
        self.requests = []

    async def _read_holding_range_async(self, start: int, count: int, dongle_serial=None) -> bytes:
        self.requests.append((start, count, dongle_serial))
        payload = bytearray()
        for reg in range(start, start + count):
            payload += reg.to_bytes(2, "little")
        return bytes(payload)


class TestHoldingRangeCoalescing(unittest.TestCase):
    def test_full_catalog_merges_into_two_requests(self):
        regs = [item["reg"] for item in mr.all_items()]
        self.assertEqual(
            mc.ModbusController._coalesce_ranges(regs, 125),
            [(0, 120), (120, 120)],
        )

    def test_block_cap_keeps_one_request_per_block(self):
        regs = [item["reg"] for item in mr.all_items()]
        self.assertEqual(len(mc.ModbusController._coalesce_ranges(regs, 40)), 6)

    def test_gaps_split_ranges(self):
        # reg 110 -> block 80, reg 204 -> block 200 (gap between them).
        self.assertEqual(
            mc.ModbusController._coalesce_ranges([110, 204], 125),
            [(80, 40), (200, 40)],
        )

    def test_read_items_full_catalog_uses_two_requests_and_parses(self):
        ctrl = _FakeReadController()
        result = asyncio.run(ctrl.read_items([item["key"] for item in mr.all_items()]))
        self.assertEqual(ctrl.requests, [(0, 120, None), (120, 120, None)])
        for item in mr.all_items():
            self.assertEqual(result[item["key"]], mr.extract_value(item, item["reg"]))

    def test_read_items_respects_lower_max_count(self):
        ctrl = _FakeReadController()
        ctrl._HOLDING_MAX_COUNT = 40
        asyncio.run(ctrl.read_items([item["key"] for item in mr.all_items()]))
        self.assertEqual(len(ctrl.requests), 6)

    def test_read_items_passes_dongle_serial(self):
        ctrl = _FakeReadController()
        asyncio.run(ctrl.read_items(["buzzer"], dongle_serial="DONGLE-A"))
        self.assertEqual(ctrl.requests, [(80, 40, "DONGLE-A")])


class _FakeWriteController(mc.ModbusController):
    """Records writes and block reads; simulates a write echo."""

    ECHO_RAW = 0x42

    def __init__(self):
        super().__init__()
        self.mode = mc.MODE_DONGLE
        self.available = True
        self._dongle_serial = "1234567890"
        self._inverter_serial = "ABCDEFGHIJ"
        self.executed = []
        self.read_calls = []
        self.verify_read_return = 0x1111

    async def execute(self, frame, expected_fn, dongle_serial=None) -> int:
        self.executed.append((expected_fn, bytes(frame), dongle_serial))
        return self.ECHO_RAW

    async def _read_holding_async(self, register, dongle_serial=None, use_cache=True) -> int:
        self.read_calls.append((register, dongle_serial))
        return self.verify_read_return


class TestWriteItemVerification(unittest.TestCase):
    def test_full_register_item_skips_verify_read(self):
        ctrl = _FakeWriteController()
        item = mr.get_item("grid_export_percent")
        result = asyncio.run(ctrl.write_item(item, 42))
        self.assertEqual(ctrl.read_calls, [])
        self.assertEqual(result, mr.extract_value(item, ctrl.ECHO_RAW))

    def test_rmw_item_skips_verify_read(self):
        ctrl = _FakeWriteController()
        item = mr.get_item("buzzer")
        result = asyncio.run(ctrl.write_item(item, True))
        # One read for RMW only; no post-write verify read.
        self.assertEqual(len(ctrl.read_calls), 1)
        self.assertEqual(result, mr.extract_value(item, ctrl.ECHO_RAW))

    def test_verify_flagged_item_keeps_verify_read(self):
        ctrl = _FakeWriteController()
        item = mr.get_item("hybrid")
        result = asyncio.run(ctrl.write_item(item, True, confirm=True))
        # RMW read + post-write verify read.
        self.assertEqual(len(ctrl.read_calls), 2)
        self.assertEqual(result, mr.extract_value(item, ctrl.verify_read_return))


class _FakeCachedWriteController(mc.ModbusController):
    """Reads return register==value payloads (served via the TTL cache) and
    execute() simulates a write echo."""

    ECHO_RAW = 0x42

    def __init__(self):
        super().__init__()
        self.mode = mc.MODE_DONGLE
        self.available = True
        self._dongle_serial = "1234567890"
        self._inverter_serial = "ABCDEFGHIJ"
        self.requests = []
        self.executed = []

    async def _fetch_holding_range(self, start: int, count: int, dongle_serial=None) -> bytes:
        self.requests.append((start, count, dongle_serial))
        payload = bytearray()
        for reg in range(start, start + count):
            payload += reg.to_bytes(2, "little")
        return bytes(payload)

    async def execute(self, frame, expected_fn, dongle_serial=None) -> int:
        self.executed.append((expected_fn, bytes(frame), dongle_serial))
        return self.ECHO_RAW


class TestReadCache(unittest.TestCase):
    def test_second_read_is_cached(self):
        ctrl = _FakeCachedWriteController()
        keys = [item["key"] for item in mr.all_items()]
        asyncio.run(ctrl.read_items(keys))
        self.assertEqual(ctrl.requests, [(0, 120, None), (120, 120, None)])
        asyncio.run(ctrl.read_items(keys))
        self.assertEqual(ctrl.requests, [(0, 120, None), (120, 120, None)])

    def test_expired_entry_is_refetched(self):
        ctrl = _FakeCachedWriteController()
        ctrl._read_cache_ttl = 0.0
        keys = [item["key"] for item in mr.all_items()]
        asyncio.run(ctrl.read_items(keys))
        asyncio.run(ctrl.read_items(keys))
        self.assertEqual(len(ctrl.requests), 4)

    def test_write_patches_cached_payload(self):
        ctrl = _FakeCachedWriteController()
        keys = [item["key"] for item in mr.all_items()]
        asyncio.run(ctrl.read_items(keys))
        item = mr.get_item("grid_export_percent")
        result = asyncio.run(ctrl.write_item(item, 42))
        result2 = asyncio.run(ctrl.read_items(keys))
        # Write patches the cached payload → no cold re-read, and reads see it.
        self.assertEqual(ctrl.requests, [(0, 120, None), (120, 120, None)])
        self.assertEqual(result, mr.extract_value(item, ctrl.ECHO_RAW))
        self.assertEqual(result2["grid_export_percent"], mr.extract_value(item, ctrl.ECHO_RAW))

    def test_use_cache_false_forces_serial_read(self):
        ctrl = _FakeCachedWriteController()
        asyncio.run(ctrl.read_items(["hybrid"]))  # block 0 cached
        asyncio.run(ctrl._read_holding_range_async(0, 40, use_cache=True))
        asyncio.run(ctrl._read_holding_range_async(0, 40, use_cache=False))
        self.assertEqual([(s, c) for s, c, _ in ctrl.requests], [(0, 40), (0, 40)])


if __name__ == "__main__":
    unittest.main()