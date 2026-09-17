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
import time
from typing import Optional

import socket_client
import modbus_registers
import modbus_service
from modbus_registers import extract_value
from modbus_service import FN_READ_HOLDING, FN_WRITE_SINGLE

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
            # SERVER mode availability depends only on the wired transport; the
            # dongle serial / inverter serial are resolved per connection
            # (registered dongles in multi-tenant mode), so they are not known
            # from config alone.
            self._dongle_host = ""
            self._dongle_port = 0
            self.available = self._server is not None
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
        # In SERVER mode the serials are resolved per connection (registered
        # dongles), so availability only depends on the wired transport.
        self.available = server is not None

    def _resolved_serials(self, dongle_serial=None):
        """Return the (dongle_serial, inverter_serial) pair to target.

        In SERVER mode the identity belongs to the routed connection; fall back
        to the configured serials when no connection matches (single-dongle
        deployments that only configure the serials).
        """
        if self.mode == MODE_SERVER and self._server is not None:
            pair = self._server.connection_serials(dongle_serial)
            if pair is not None:
                return pair
        return self._dongle_serial, self._inverter_serial

    @property
    def status(self) -> dict:
        return {"mode": self.mode, "available": self.available}

    def _require_available(self) -> None:
        if not self.available:
            raise ModbusUnavailableError(
                "Modbus is not available in %s mode" % self.mode
            )

    async def execute(self, frame: bytes, expected_fn: int, dongle_serial=None) -> int:
        """Send a request frame and return the parsed response value."""
        raw = await self.execute_raw(frame, expected_fn, dongle_serial=dongle_serial)
        return modbus_service.parse_response(raw, expected_fn)

    async def execute_raw(self, frame: bytes, expected_fn: int, dongle_serial=None) -> bytes:
        """Send a request frame and return the raw response bytes."""
        self._require_available()
        logger.debug(
            "Modbus request fn=0x%02x reg=%s frame=%s mode=%s dongle_serial=%s",
            expected_fn,
            modbus_service.request_register(frame),
            bytes(frame).hex(),
            self.mode,
            dongle_serial,
        )
        if self.mode == MODE_SERVER:
            return await self._server_execute_raw(frame, expected_fn, dongle_serial)
        return await asyncio.to_thread(self._blocking_execute_raw, frame, expected_fn)

    def _blocking_execute_raw(self, frame: bytes, expected_fn: int) -> bytes:
        frame_bytes = bytes(frame)
        try:
            expected_register = modbus_service.request_register(frame_bytes)
        except modbus_service.ModbusError:
            expected_register = None
        with self._lock:
            sock = socket_client.connect(self._dongle_host, self._dongle_port)
            try:
                sock.settimeout(DEFAULT_TIMEOUT)
                sock.sendall(frame_bytes)
                deadline = time.monotonic() + DEFAULT_TIMEOUT
                data = b""
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        sock.settimeout(remaining)
                        chunk = sock.recv(1024)
                    except (TimeoutError, socket.timeout):
                        break
                    if not chunk:
                        break
                    data += chunk
                    # The dongle may push telemetry / other-transaction replies on
                    # any open connection (lxp-bridge wait_for_reply). Consume
                    # complete frames and keep only the one matching our request.
                    while len(data) >= 8:
                        expected = modbus_service.to_int(data[4:6]) + modbus_service._FRAME_LENGTH_ADJUST
                        if expected > 4096 or expected < 8:
                            data = data[1:]  # resync one byte at a time
                            continue
                        if len(data) < expected:
                            break
                        candidate, data = data[:expected], data[expected:]
                        try:
                            fn = modbus_service.response_function(candidate)
                        except modbus_service.ModbusError:
                            continue  # not a parsable frame; drop it
                        if fn != expected_fn:
                            logger.warning(
                                "Modbus discarding unrelated frame fn=0x%02x (wanted 0x%02x reg=%s)",
                                fn, expected_fn, expected_register,
                            )
                            continue
                        if (
                            expected_register is not None
                            and modbus_service.response_register(candidate) != expected_register
                        ):
                            logger.warning(
                                "Modbus discarding frame for reg=%s (wanted reg=%s)",
                                modbus_service.response_register(candidate), expected_register,
                            )
                            continue
                        logger.debug(
                            "Modbus reply fn=0x%02x reg=%s len=%d frame=%s",
                            expected_fn,
                            expected_register,
                            len(candidate),
                            candidate.hex(),
                        )
                        return candidate
            except (TimeoutError, socket.timeout):
                logger.warning(
                    "Modbus %s reg=%s: timeout waiting for reply (got %d bytes)",
                    expected_fn,
                    expected_register,
                    len(data),
                )
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
        raise ModbusTimeoutError("No Modbus response from dongle")

    async def _server_execute_raw(self, frame: bytes, expected_fn: int, dongle_serial=None) -> bytes:
        if self._server is None or self._server_loop is None:
            raise ModbusUnavailableError("Server transport not initialized")
        future = asyncio.run_coroutine_threadsafe(
            self._server.request_modbus(
                bytes(frame), expected_fn, return_raw=True, dongle_serial=dongle_serial
            ),
            self._server_loop,
        )
        return await asyncio.wrap_future(future)

    # Catalog registers are settings stored in the holding register space, so
    # reads go through function code 0x03 (ReadHolding) as the capture/reference
    # implementations (lxp-bridge, luxpower-ha-integration) do.  The input
    # register space (0x04 blocks at 0/40/80/120 serviced by Dongle.read_input)
    # holds live telemetry only — the same register numbers mean different
    # things there, so reading the catalog via input blocks yields wrong values.
    _HOLDING_BLOCK_COUNT = 40

    @staticmethod
    def _block_for(register: int) -> tuple:
        base = (register // ModbusController._HOLDING_BLOCK_COUNT) * ModbusController._HOLDING_BLOCK_COUNT
        count = ModbusController._HOLDING_BLOCK_COUNT
        return base, count, register - base

    async def _read_holding_block_async(self, base: int, count: int, dongle_serial=None) -> bytes:
        dongle, inverter = self._resolved_serials(dongle_serial)
        frame = modbus_service.build_read_holding_request(
            dongle, inverter, register=base, count=count
        )
        raw = await self.execute_raw(frame, FN_READ_HOLDING, dongle_serial=dongle_serial)
        register, payload = modbus_service.read_response_values(raw, FN_READ_HOLDING)
        logger.debug(
            "Read holding block reg=%s count=%s: echo_reg=%s payload=%d bytes",
            base, count, register, len(payload),
        )
        return payload

    async def _read_holding_async(self, register: int, dongle_serial=None) -> int:
        base, count, offset = self._block_for(register)
        payload = await self._read_holding_block_async(base, count, dongle_serial=dongle_serial)
        start = offset * 2
        if start + 2 > len(payload):
            raise modbus_service.ModbusTruncatedFrame(
                "Holding block %s:%s too short (%d bytes) for register %s"
                % (base, count, len(payload), register)
            )
        return modbus_service.to_int(payload[start:start + 2])

    async def read_items(self, keys, dongle_serial=None) -> dict:
        """Read multiple catalog items via the holding register blocks."""
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

        # Read each needed holding block once, then build a reg→raw map.
        # Blocks are keyed by base register (0, 40, 80, 120, ...).
        blocks: dict[int, bytes] = {}
        for item in items:
            base, count, offset = self._block_for(item["reg"])
            if base not in blocks:
                blocks[base] = await self._read_holding_block_async(base, count, dongle_serial=dongle_serial)

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

    async def write_item(self, item: dict, value, confirm: bool = True, dongle_serial=None) -> object:
        """Validate and write a catalog item, returning the new display value."""
        if item.get("danger") and not confirm:
            raise ValueError("Confirmation is required for danger item %s" % item["key"])

        raw_part = modbus_registers.encode_value(item, value)

        shift, mask = modbus_registers.mask_for(item)
        needs_rmw = mask != 0xFFFF

        if needs_rmw:
            current_raw = await self._read_holding_async(item["reg"], dongle_serial=dongle_serial)
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

        dongle, inverter = self._resolved_serials(dongle_serial)
        frame = modbus_service.build_write_single_request(
            dongle, inverter, item["reg"], raw
        )
        await self.execute(frame, FN_WRITE_SINGLE, dongle_serial=dongle_serial)

        fresh_raw = await self._read_holding_async(item["reg"], dongle_serial=dongle_serial)
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