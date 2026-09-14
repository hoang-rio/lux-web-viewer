"""LuxPower local TCP protocol service for Modbus register reads/writes.

The protocol envelope is identical to the ReadInput frames built in
dongle_handler.py (TCP_FUNCTION_TRANSLATE=194):

    A1 1A | protocol u16 LE | frame_length-6 u16 LE | 01 | C2 | dongle_serial(10) | data | CRC16-Modbus

The "data" portion is the inner frame:

    [len u16 LE][action u8=0 (client->inverter)][device_fn u8][inverter_serial(10)][register u16 LE][payload]

where device_fn is the standard Modbus function code carried inside the
translated-data frame: 0x03 ReadHolding, 0x04 ReadInput, 0x06 WriteSingle,
0x10 WriteMulti. CRC16-Modbus is computed over data[2:] (action .. payload).
"""

import struct
from typing import Optional

A1 = 161
DATA_START = 26

TCP_FUNCTION_TRANSLATE = 194
ACTION_CLIENT_TO_INVERTER = 0

FN_READ_HOLDING = 0x03
FN_READ_INPUT = 0x04
FN_WRITE_SINGLE = 0x06
FN_WRITE_MULTI = 0x10

# Header layout offsets within the outer TCP frame.
_HEADER_LEN = 18  # 2 + 2 + 2 + 1 + 1 + 10
_FRAME_LENGTH_OFFSET = 4
_FRAME_LENGTH_ADJUST = 6  # header field holds frame_length - 6


class ModbusError(Exception):
    """Base error raised by the Modbus service."""


class ModbusExceptionResponse(ModbusError):
    def __init__(self, function: int, code: int):
        self.function = function
        self.code = code
        super().__init__("Modbus exception response 0x{:02x} code 0x{:02x}".format(function, code))


class ModbusWrongFunction(ModbusError):
    pass


class ModbusTruncatedFrame(ModbusError):
    pass


def to_int(byte_slice) -> int:
    """Little-endian integer from a byte slice (list or bytes)."""
    return sum(b << (idx * 8) for idx, b in enumerate(byte_slice))


def crc16_modbus(payload: bytes) -> int:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def serial_bytes(serial: str) -> bytes:
    raw = str(serial).encode("ascii", errors="ignore")
    return raw[:10].ljust(10, b"\x00")


def build_inner_frame(inverter_serial: str, device_fn: int, payload: bytes) -> bytes:
    """Build the inner translated-data frame (with CRC) for a request."""
    data = bytearray([0, 0, 0, device_fn])
    data.extend(serial_bytes(inverter_serial))
    data.extend(payload)
    data[0:2] = len(data).to_bytes(2, "little", signed=False)
    data.extend(crc16_modbus(data[2:]).to_bytes(2, "little", signed=False))
    return bytes(data)


def build_frame(dongle_serial: str, inverter_serial: str, device_fn: int, payload: bytes, protocol: int = 1) -> bytes:
    """Build the complete TCP frame for a request."""
    data = build_inner_frame(inverter_serial, device_fn, payload)
    frame_length = _HEADER_LEN + len(data)
    frame = bytearray([A1, DATA_START])
    frame.extend(int(protocol).to_bytes(2, "little", signed=False))
    frame.extend(int(frame_length - _FRAME_LENGTH_ADJUST).to_bytes(2, "little", signed=False))
    frame.extend([1, TCP_FUNCTION_TRANSLATE])
    frame.extend(serial_bytes(dongle_serial))
    frame.extend(data)
    return bytes(frame)


def build_response_frame(
    dongle_serial: str,
    inverter_serial: str,
    device_fn: int,
    register: int,
    payload: bytes = b"",
    protocol: int = 1,
) -> bytes:
    """Build a response TCP frame matching the real dongle reply layout.

    The response inner shares the request inner layout:
    [len u16][action=0][device_fn u8][inverter_serial(10)][register u16][payload].
    For a read reply payload holds an optional value-length byte followed by
    the value bytes; for a 0x06 echo the payload holds the written value;
    for a 0x10 echo the payload holds the register count. An exception reply
    passes fn|0x80 as device_fn with a single exception-code byte in payload.
    """
    data = bytearray([0, 0, 0, device_fn])
    data.extend(serial_bytes(inverter_serial))
    data.extend(int(register).to_bytes(2, "little", signed=False))
    data.extend(payload)
    data[0:2] = len(data).to_bytes(2, "little", signed=False)
    data.extend(crc16_modbus(data[2:]).to_bytes(2, "little", signed=False))
    frame_length = _HEADER_LEN + len(data)
    frame = bytearray([A1, DATA_START])
    frame.extend(int(protocol).to_bytes(2, "little", signed=False))
    frame.extend(int(frame_length - _FRAME_LENGTH_ADJUST).to_bytes(2, "little", signed=False))
    frame.extend([1, TCP_FUNCTION_TRANSLATE])
    frame.extend(serial_bytes(dongle_serial))
    frame.extend(data)
    return bytes(frame)


def build_read_holding_request(
    dongle_serial: str, inverter_serial: str, register: int, count: int = 1, protocol: int = 1
) -> bytes:
    payload = int(register).to_bytes(2, "little", signed=False)
    payload += int(count).to_bytes(2, "little", signed=False)
    return build_frame(dongle_serial, inverter_serial, FN_READ_HOLDING, payload, protocol)


def build_read_input_request(
    dongle_serial: str, inverter_serial: str, register: int, count: int = 1, protocol: int = 1
) -> bytes:
    payload = int(register).to_bytes(2, "little", signed=False)
    payload += int(count).to_bytes(2, "little", signed=False)
    return build_frame(dongle_serial, inverter_serial, FN_READ_INPUT, payload, protocol)


def build_write_single_request(
    dongle_serial: str, inverter_serial: str, register: int, value: int, protocol: int = 1
) -> bytes:
    payload = int(register).to_bytes(2, "little", signed=False)
    payload += int(value).to_bytes(2, "little", signed=False)
    return build_frame(dongle_serial, inverter_serial, FN_WRITE_SINGLE, payload, protocol)


def build_write_multi_request(
    dongle_serial: str, inverter_serial: str, register: int, values, protocol: int = 1
) -> bytes:
    values = [int(v) for v in values]
    byte_count = len(values) * 2
    payload = int(register).to_bytes(2, "little", signed=False)
    payload += int(len(values)).to_bytes(2, "little", signed=False)
    payload += bytes([byte_count])
    for value in values:
        payload += int(value).to_bytes(2, "little", signed=False)
    return build_frame(dongle_serial, inverter_serial, FN_WRITE_MULTI, payload, protocol)


def frame_length_expected(frame: bytes) -> int:
    """Return total frame length expected from the header length field."""
    if len(frame) < _FRAME_LENGTH_OFFSET + 2:
        return 0
    return to_int(frame[_FRAME_LENGTH_OFFSET:_FRAME_LENGTH_OFFSET + 2]) + _FRAME_LENGTH_ADJUST


def _response_subject(frame):
    """Return the response inner frame (request header + inner) without its CRC.

    A dongle response inner matches the request inner layout:
        [len u16][action u8][device_fn u8][inverter_serial(10)][register u16][payload]
    so the function sits at subject[3] and the register at subject[14:16].
    This mirrors dongle_handler.read_input which strips a further 2 bytes
    (data[1] = function, data[12:14] = register).
    """
    if isinstance(frame, bytes):
        frame = list(frame)
    if len(frame) < _HEADER_LEN + 4:
        raise ModbusTruncatedFrame("Frame too short for response subject")
    return frame[_HEADER_LEN: len(frame) - 2]


def response_function(frame) -> int:
    """Return the Modbus function code carried in a response inner frame."""
    subject = _response_subject(frame)
    return subject[3]


def response_register(frame) -> int:
    """Return the register address echoed in a response inner frame."""
    subject = _response_subject(frame)
    if len(subject) < 16:
        raise ModbusTruncatedFrame("Inner frame too short for register")
    return to_int(subject[14:16])


def request_register(frame: bytes) -> int:
    """Return the register address from a request frame (inner data layout)."""
    if len(frame) < _HEADER_LEN + 16:
        raise ModbusTruncatedFrame("Request frame too short for register")
    data = frame[_HEADER_LEN:]
    return to_int(data[14:16])


def parse_response(frame, expected_fn: int) -> int:
    """Parse a response frame and return the register value.

    Supports read (0x03/0x04) responses with the value-length byte and
    write (0x06/0x10) echo responses.
    """
    if isinstance(frame, bytes):
        frame = list(frame)
    if len(frame) < _HEADER_LEN:
        raise ModbusTruncatedFrame("Frame too short: %d bytes" % len(frame))

    # Strip outer header + trailing CRC. Inner layout (request and response):
    # [len u16][action u8][device_fn u8][inverter_serial(10)][register u16][payload]
    subject = frame[_HEADER_LEN: len(frame) - 2]
    if len(subject) < 4:
        raise ModbusTruncatedFrame("Inner frame too short: %d bytes" % len(subject))

    function = subject[3]
    if function & 0x80:
        code = subject[14] if len(subject) > 14 else 0
        raise ModbusExceptionResponse(expected_fn, code)

    if len(subject) < 16:
        raise ModbusTruncatedFrame("Inner frame too short: %d bytes" % len(subject))

    if function != expected_fn:
        raise ModbusWrongFunction(
            "Expected function 0x%02x but got 0x%02x" % (expected_fn, function)
        )

    register = to_int(subject[14:16])

    if expected_fn in (FN_READ_HOLDING, FN_READ_INPUT):
        # Most responses include a value-length byte; detect it when it matches
        # the remaining bytes after it (matching dongle_handler.read_input logic).
        remaining_after_register = len(subject) - 16
        value_len = None
        if remaining_after_register >= 1:
            advertised = subject[16]
            if advertised == remaining_after_register - 1:
                value_len = advertised
        start = 17 if value_len is not None else 16
        return to_int(subject[start:start + 2])

    if expected_fn == FN_WRITE_SINGLE:
        # Echo of register + written value (no length byte).
        if len(subject) - 16 >= 2:
            return to_int(subject[16:18])
        return 0

    if expected_fn == FN_WRITE_MULTI:
        # Echo of register + count of registers written.
        count = to_int(subject[16:18]) if len(subject) - 16 >= 2 else 0
        return count

    raise ModbusError("Unsupported function 0x%02x" % expected_fn)