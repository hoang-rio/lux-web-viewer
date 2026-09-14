"""Transport controller for Modbus register operations.

Provides a single entry point for the /modbus REST endpoints that works in all
three WORKING_MODE configurations:

  DONGLE  - short-lived TCP connection to the dongle per operation, serialized
            with a threading lock (independent of the polling socket).
  SERVER  - requests are interleaved with the polling loop on the existing
            dongle connection via DongleServer (see `request_modbus`).
  HTTP    - Modbus is not available; endpoints report it as unavailable.

app.py calls ``configure(config)`` at startup and ``set_server(...)`` in SERVER
mode so the controller knows how to reach the running DongleServer.
"""

import asyncio
import logging
import socket
import threading
from typing import Optional

import socket_client
import modbus_registers
import modbus_service
from modbus_registers import extract_value
from modbus_service import FN_READ_INPUT, FN_WRITE_SINGLE

logger = logging.getLogger(__file__)

MODE_DONGLE = "DONGLE"
MODE_SERVER = "SERVER"
MODE_HTTP = "HTTP"
MODE_NOT_CONFIGURED = "NOT_CONFIGURED"

DEFAULT_TIMEOUT = 5.0


class ModbusUnavailableError(RuntimeError):
    """Raised when Modbus operations cannot be performed in the current mode."""


class ModbusTimeoutError(RuntimeError):
    """Raised when the device does not answer within the timeout."""


class ModbusController:
    def __init__(self) -> None:
        self.mode = MODE_NOT_CONFIGURED
        self.available = False
        self._dongle_host = ""
        self._dongle_port = 0
        self._dongle_serial = ""
        self._inverter_serial = ""
        self._lock = threading.Lock()
        self._server = None
        self._server_loop = None

    def configure(self, config: dict) -> None:
        self._dongle_serial = str(config.get("DONGLE_SERIAL") or "")
        self._inverter_serial = str(config.get("INVERT_SERIAL") or "")
        mode = (config.get("WORKING_MODE") or MODE_DONGLE).strip().upper()
        self.mode = mode
        if mode == MODE_HTTP:
            self.available = False
        elif mode == MODE_SERVER:
            self._dongle_host = ""
            self._dongle_port = 0
            self.available = self._server is not None and bool(self._dongle_serial) and bool(self._inverter_serial)
        else:
            self._dongle_host = str(config.get("DONGLE_TCP_HOST") or "")
            try:
                self._dongle_port = int(config.get("DONGLE_TCP_PORT") or 8000)
            except (TypeError, ValueError):
                self._dongle_port = 8000
            self.available = bool(self._dongle_host) and bool(self._dongle_serial) and bool(self._inverter_serial)
        logger.info("ModbusController mode=%s available=%s", self.mode, self.available)

    def set_server(self, server, main_loop) -> None:
        self._server = server
        self._server_loop = main_loop or asyncio.get_running_loop()
        self.available = server is not None and bool(self._dongle_serial) and bool(self._inverter_serial)

    @property
    def status(self) -> dict:
        return {"mode": self.mode, "available": self.available}

    def _require_available(self) -> None:
        if not self.available:
            raise ModbusUnavailableError(
                "Modbus is not available in %s mode" % self.mode
            )

    async def execute(self, frame: bytes, expected_fn: int) -> int:
        """Send a request frame and return the parsed response value."""
        raw = await self.execute_raw(frame, expected_fn)
        return modbus_service.parse_response(raw, expected_fn)

    async def execute_raw(self, frame: bytes, expected_fn: int) -> bytes:
        """Send a request frame and return the raw response bytes."""
        self._require_available()
        logger.debug(
            "Modbus request fn=0x%02x reg=%s frame=%s mode=%s",
            expected_fn,
            modbus_service.request_register(frame),
            bytes(frame).hex(),
            self.mode,
        )
        if self.mode == MODE_SERVER:
            return await self._server_execute_raw(frame, expected_fn)
        return await asyncio.to_thread(self._blocking_execute_raw, frame, expected_fn)

    def _blocking_execute_raw(self, frame: bytes, expected_fn: int) -> bytes:
        frame_bytes = bytes(frame)
        with self._lock:
            sock = socket_client.connect(self._dongle_host, self._dongle_port)
            try:
                sock.settimeout(DEFAULT_TIMEOUT)
                sock.sendall(frame_bytes)
                data = b""
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    data += chunk
                    # The response carries its own length in the header; a read
                    # reply is one byte longer than the request (value-length byte).
                    if len(data) >= 8:
                        expected = modbus_service.to_int(data[4:6]) + modbus_service._FRAME_LENGTH_ADJUST
                        if len(data) >= expected or expected > 4096:
                            break
            except (TimeoutError, socket.timeout):
                logger.warning(
                    "Modbus %s reg=%s: timeout waiting for reply (got %d bytes)",
                    expected_fn,
                    modbus_service.request_register(frame),
                    len(data),
                )
                data = data or b""
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
        if not data:
            raise ModbusTimeoutError("No Modbus response from dongle")
        logger.debug(
            "Modbus reply fn=0x%02x reg=%s len=%d frame=%s",
            expected_fn,
            modbus_service.request_register(frame),
            len(data),
            data.hex(),
        )
        return data

    async def _server_execute_raw(self, frame: bytes, expected_fn: int) -> bytes:
        if self._server is None or self._server_loop is None:
            raise ModbusUnavailableError("Server transport not initialized")
        future = asyncio.run_coroutine_threadsafe(
            self._server.request_modbus(bytes(frame), expected_fn, return_raw=True),
            self._server_loop,
        )
        return await asyncio.wrap_future(future)

    # Input register blocks served by the dongle (see Dongle.read_input):
    # register base is one of 0/40/80/120 and each block holds 40 registers.
    _INPUT_BLOCK_COUNT = 40

    @staticmethod
    def _block_for(register: int) -> tuple:
        base = (register // ModbusController._INPUT_BLOCK_COUNT) * ModbusController._INPUT_BLOCK_COUNT
        count = ModbusController._INPUT_BLOCK_COUNT
        return base, count, register - base

    async def _read_input_block_async(self, base: int, count: int) -> bytes:
        frame = modbus_service.build_read_input_request(
            self._dongle_serial, self._inverter_serial, register=base, count=count
        )
        payload = modbus_service.read_block_payload(
            await self.execute_raw(frame, FN_READ_INPUT), FN_READ_INPUT, base
        )
        logger.debug(
            "Read input block reg=%s count=%s: %d payload bytes",
            base, count, len(payload),
        )
        return payload

    async def read_input_async(self, register: int) -> int:
        base, count, offset = self._block_for(register)
        payload = await self._read_input_block_async(base, count)
        start = offset * 2
        if start + 2 > len(payload):
            raise modbus_service.ModbusTruncatedFrame(
                "Input block %s:%s too short (%d bytes) for register %s"
                % (base, count, len(payload), register)
            )
        return modbus_service.to_int(payload[start:start + 2])

    async def read_items(self, keys) -> dict:
        """Read multiple catalog items via the 40-register input blocks."""
        items = []
        seen_regs = set()
        for key in keys:
            item = modbus_registers.get_item(key)
            if item is None:
                logger.warning("Read: unknown register key %r", key)
                continue
            if item["reg"] in seen_regs:
                continue
            seen_regs.add(item["reg"])
            items.append(item)

        logger.debug(
            "Read %d register items in %d blocks",
            len(items),
            len(set(self._block_for(i["reg"])[0] for i in items)),
        )

        # Read each needed input block once, then build a reg→raw map.
        # Blocks are keyed by base register (0, 40, 80, 120, ...).
        blocks: dict[int, bytes] = {}
        for item in items:
            base, count, offset = self._block_for(item["reg"])
            if base not in blocks:
                blocks[base] = await self._read_input_block_async(base, count)

        # Build reg→raw from blocks.  payload[2*offset] is the register value.
        reg_raw: dict[int, int] = {}
        for base, block in blocks.items():
            for item in items:
                ibase, _, ioffset = self._block_for(item["reg"])
                if ibase != base:
                    continue
                start = ioffset * 2
                if start + 2 <= len(block):
                    reg_raw[item["reg"]] = modbus_service.to_int(block[start : start + 2])
                else:
                    logger.warning(
                        "Register %s out of block range: offset=%s block_len=%s",
                        item["reg"], start, len(block),
                    )

        result = {}
        for key in keys:
            item = modbus_registers.get_item(key)
            if item is None:
                result[key] = None
                continue
            raw = reg_raw.get(item["reg"])
            if raw is None:
                result[key] = None
                continue
            result[key] = extract_value(item, raw)
        return result

    async def write_item(self, item: dict, value, confirm: bool = True) -> object:
        """Validate and write a catalog item, returning the new display value."""
        if item.get("danger") and not confirm:
            raise ValueError("Confirmation is required for danger item %s" % item["key"])

        raw_part = modbus_registers.encode_value(item, value)

        shift, mask = modbus_registers.mask_for(item)
        needs_rmw = mask != 0xFFFF

        if needs_rmw:
            current_raw = await self.read_input_async(item["reg"])
            raw = modbus_registers.merge_raw(item, current_raw, raw_part)
            logger.info(
                "Modbus write%s key=%s value=%r -> reg=%s raw=0x%04x (rmw from 0x%04x)",
                " (danger)" if item.get("danger") else "",
                item["key"], value, item["reg"], raw, current_raw,
            )
        else:
            raw = raw_part
            logger.info(
                "Modbus write key=%s value=%r -> reg=%s raw=0x%04x",
                item["key"], value, item["reg"], raw,
            )

        frame = modbus_service.build_write_single_request(
            self._dongle_serial, self._inverter_serial, item["reg"], raw
        )
        await self.execute(frame, FN_WRITE_SINGLE)

        fresh_raw = await self.read_input_async(item["reg"])
        logger.debug(
            "Modbus write verify key=%s reg=%s fresh_raw=0x%04x value=%r",
            item["key"], item["reg"], fresh_raw,
            modbus_registers.extract_value(item, fresh_raw),
        )
        return modbus_registers.extract_value(item, fresh_raw)


controller = ModbusController()


def configure(config: dict) -> None:
    controller.configure(config)


def set_server(server, main_loop) -> None:
    controller.set_server(server, main_loop)