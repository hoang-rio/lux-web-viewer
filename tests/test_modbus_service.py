import unittest

import modbus_service as m


class TestSerialBytes(unittest.TestCase):
    def test_pads_short_serial(self):
        self.assertEqual(m.serial_bytes("12345"), b"12345\x00\x00\x00\x00\x00")

    def test_truncates_long_serial(self):
        self.assertEqual(m.serial_bytes("A" * 20), b"A" * 10)

    def test_ignores_non_ascii(self):
        raw = m.serial_bytes("ab\u0103cd")
        self.assertEqual(raw[0:2], b"ab")


class TestCrc16(unittest.TestCase):
    def test_known_vector(self):
        # Canonical CRC-16/MODBUS check value.
        self.assertEqual(m.crc16_modbus(b"123456789"), 0x4B37)


class TestBuildRequest(unittest.TestCase):
    DONGLE = "1234567890"
    INV = "ABCDEFGHIJ"

    def _decode(self, frame):
        frame = list(frame)
        self.assertEqual(frame[0], m.A1)
        self.assertEqual(frame[1], m.DATA_START)
        self.assertEqual(frame[7], m.TCP_FUNCTION_TRANSLATE)
        self.assertEqual(frame[6], 1)
        protocol = m.to_int(frame[2:4])
        length_field = m.to_int(frame[4:6])
        dongle = bytes(frame[8:18])
        data = frame[18:]
        return protocol, length_field, dongle, data

    def _check_inner(self, data):
        data = list(data)
        inner_len = m.to_int(data[0:2])
        self.assertEqual(inner_len, len(data) - 2)
        self.assertEqual(data[2], m.ACTION_CLIENT_TO_INVERTER)
        crc = m.crc16_modbus(bytes(data[2:-2]))
        self.assertEqual(m.to_int(data[-2:]), crc)
        self.assertEqual(bytes(data[4:14]), m.serial_bytes(self.INV))
        return m.to_int(data[14:16])

    def test_read_holding_request(self):
        frame = m.build_read_holding_request(self.DONGLE, self.INV, 0x2AF0)
        protocol, length_field, dongle, data = self._decode(frame)
        self.assertEqual(protocol, 1)
        self.assertEqual(length_field, len(frame) - m._FRAME_LENGTH_ADJUST)
        self.assertEqual(dongle[:10], m.serial_bytes(self.DONGLE))
        self.assertEqual(self._check_inner(data), 0x2AF0)
        self.assertEqual(data[3], m.FN_READ_HOLDING)
        # payload: register + count(1)
        self.assertEqual(m.to_int(data[14:16]), 0x2AF0)
        self.assertEqual(m.to_int(data[16:18]), 1)

    def test_write_single_request(self):
        frame = m.build_write_single_request(self.DONGLE, self.INV, 0x2AF0, 0x1234)
        _, _, _, data = self._decode(frame)
        self.assertEqual(data[3], m.FN_WRITE_SINGLE)
        self.assertEqual(m.to_int(data[14:16]), 0x2AF0)
        self.assertEqual(m.to_int(data[16:18]), 0x1234)

    def test_write_multi_request(self):
        frame = m.build_write_multi_request(self.DONGLE, self.INV, 0x100, [0x0001, 0x0002])
        _, _, _, data = self._decode(frame)
        self.assertEqual(data[3], m.FN_WRITE_MULTI)
        self.assertEqual(m.to_int(data[14:16]), 0x100)
        self.assertEqual(m.to_int(data[16:18]), 2)
        self.assertEqual(data[18], 4)
        self.assertEqual(m.to_int(data[19:21]), 0x0001)
        self.assertEqual(m.to_int(data[21:23]), 0x0002)

    def test_frame_length_expected(self):
        frame = m.build_read_holding_request(self.DONGLE, self.INV, 1)
        self.assertEqual(m.frame_length_expected(frame), len(frame))
        self.assertEqual(m.frame_length_expected(b""), 0)

    def test_protocol_override(self):
        frame = m.build_read_input_request(self.DONGLE, self.INV, 0x300, protocol=2)
        self.assertEqual(m.to_int(frame[2:4]), 2)


class _Fixture:
    """Shared serials used to build response frames."""

    DONGLE = "1234567890"
    INV = "ABCDEFGHIJ"


def build_response(expected_fn, register, payload):
    """Build a response frame with the real dongle reply layout.

    payload lands right after the register field inside the inner frame, e.g.
    [value_len, value_lo, value_hi] for reads, the written value for a 0x06
    echo, or the register count for a 0x10 echo.
    """
    return m.build_response_frame(
        dongle_serial=_Fixture.DONGLE,
        inverter_serial=_Fixture.INV,
        device_fn=expected_fn,
        register=register,
        payload=payload,
    )


def build_exception_response(expected_fn, code):
    """Build an exception reply: [len][action][fn|0x80][serial][code]."""
    inner = [0, 0, 0, expected_fn | 0x80]
    inner += list(m.serial_bytes(_Fixture.INV))
    inner += [code]
    inner[0:2] = len(inner).to_bytes(2, "little")
    crc = m.crc16_modbus(bytes(inner[2:])).to_bytes(2, "little")
    inner += list(crc)
    frame = bytearray([m.A1, m.DATA_START])
    frame.extend((1).to_bytes(2, "little"))
    frame.extend((len(inner) + m._HEADER_LEN - m._FRAME_LENGTH_ADJUST).to_bytes(2, "little"))
    frame.extend([1, m.TCP_FUNCTION_TRANSLATE])
    frame.extend(m.serial_bytes(_Fixture.DONGLE))
    frame.extend(inner)
    return bytes(frame)


class TestParseResponse(unittest.TestCase):
    def test_read_holding_with_value_length_byte(self):
        frame = build_response(m.FN_READ_HOLDING, 0x2AF0, bytes([2, 0x20, 0x80]))
        value = m.parse_response(frame, m.FN_READ_HOLDING)
        self.assertEqual(value, 0x8020)

    def test_read_holding_without_value_length_byte(self):
        frame = build_response(m.FN_READ_HOLDING, 0x2AF0, bytes([0x20, 0x80]))
        value = m.parse_response(frame, m.FN_READ_HOLDING)
        self.assertEqual(value, 0x8020)

    def test_write_single_echo(self):
        frame = build_response(m.FN_WRITE_SINGLE, 0x2AF0, bytes([0x34, 0x12]))
        value = m.parse_response(frame, m.FN_WRITE_SINGLE)
        self.assertEqual(value, 0x1234)

    def test_write_multi_echo(self):
        frame = build_response(m.FN_WRITE_MULTI, 0x100, bytes([2, 0]))
        count = m.parse_response(frame, m.FN_WRITE_MULTI)
        self.assertEqual(count, 2)

    def test_exception_response(self):
        frame = build_exception_response(m.FN_READ_HOLDING, 0x02)
        with self.assertRaises(m.ModbusExceptionResponse) as ctx:
            m.parse_response(frame, m.FN_READ_HOLDING)
        self.assertEqual(ctx.exception.function, m.FN_READ_HOLDING)
        self.assertEqual(ctx.exception.code, 0x02)

    def test_wrong_function(self):
        frame = build_response(m.FN_READ_INPUT, 0x2AF0, bytes([2, 0x20, 0x80]))
        with self.assertRaises(m.ModbusWrongFunction):
            m.parse_response(frame, m.FN_READ_HOLDING)

    def test_truncated_frame(self):
        with self.assertRaises(m.ModbusTruncatedFrame):
            m.parse_response(b"\xa1\x1a\x00", m.FN_READ_HOLDING)

    def test_truncated_inner(self):
        # inner frame shorter than needed for the register field
        inner = [0, 0, 0, m.FN_READ_HOLDING] + [0, 0, 0, 0, 0, 0]
        inner[0:2] = len(inner).to_bytes(2, "little")
        crc = m.crc16_modbus(bytes(inner[2:])).to_bytes(2, "little")
        inner += list(crc)
        frame = bytearray([m.A1, m.DATA_START])
        frame.extend((1).to_bytes(2, "little"))
        frame.extend((len(inner) + m._HEADER_LEN - m._FRAME_LENGTH_ADJUST).to_bytes(2, "little"))
        frame.extend([1, m.TCP_FUNCTION_TRANSLATE])
        frame.extend(m.serial_bytes(_Fixture.DONGLE))
        frame.extend(inner)
        with self.assertRaises(m.ModbusTruncatedFrame):
            m.parse_response(bytes(frame), m.FN_READ_HOLDING)


class TestHelpers(unittest.TestCase):
    def test_request_register(self):
        frame = m.build_read_holding_request("1234567890", "ABCDEFGHIJ", 0x1234)
        self.assertEqual(m.request_register(frame), 0x1234)

    def test_response_register_and_function(self):
        frame = build_response(m.FN_READ_HOLDING, 0x2AF0, bytes([2, 0x20, 0x80]))
        self.assertEqual(m.response_register(frame), 0x2AF0)
        self.assertEqual(m.response_function(frame), m.FN_READ_HOLDING)

    def test_response_layout_matches_request_offsets(self):
        # register must be found at the same relative offset as in requests so
        # the DongleServer interleave can match pending requests to responses.
        dongle, inv = "1234567890", "ABCDEFGHIJ"
        req = m.build_write_single_request(dongle, inv, 0x2AF0, 0x1234)
        self.assertEqual(m.to_int(req[m._HEADER_LEN:][14:16]), 0x2AF0)
        resp = build_response(m.FN_READ_HOLDING, 0x2AF0, bytes([2, 0x20, 0x80]))
        self.assertEqual(m.response_register(resp), m.request_register(req))

    def test_exception_function_detected(self):
        frame = build_exception_response(m.FN_READ_HOLDING, 0x03)
        self.assertEqual(m.response_function(frame), m.FN_READ_HOLDING | 0x80)

    def test_roundtrip(self):
        dongle, inv, reg, value = "1111111111", "2222222222", 0x123, 0x4567
        frame = m.build_write_single_request(dongle, inv, reg, value)
        self.assertEqual(m.request_register(frame), reg)
        resp = build_response(m.FN_WRITE_SINGLE, reg, bytes([0x67, 0x45]))
        self.assertEqual(m.response_register(resp), reg)
        self.assertEqual(m.parse_response(resp, m.FN_WRITE_SINGLE), value)


if __name__ == "__main__":
    unittest.main()