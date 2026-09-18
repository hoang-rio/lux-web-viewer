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
        self._read_cache = {}
        self._read_cache_lock = threading.Lock()
        self._read_cache_ttl = self._READ_CACHE_TTL

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
        cache_ttl = config.get("MODBUS_READ_CACHE_TTL")
        try:
            self._read_cache_ttl = float(cache_ttl) if cache_ttl else self._READ_CACHE_TTL
        except (TypeError, ValueError):
            self._read_cache_ttl = self._READ_CACHE_TTL
        self._read_cache.clear()
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

    async def _server_execute_raw(self, frame: bytes, expected_fn: int) -> bytes:
        if self._server is None or self._server_loop is None:
            raise ModbusUnavailableError("Server transport not initialized")
        future = asyncio.run_coroutine_threadsafe(
            self._server.request_modbus(bytes(frame), expected_fn, return_raw=True),
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
    # Maximum registers per single 0x03 (ReadHolding) request.  The Modbus spec
    # allows 125; the device/dongle may support less.  Lower this to 40 to fall
    # back to the previous one-request-per-block behaviour.
    _HOLDING_MAX_COUNT = 125
    # Server-side TTL cache for holding-range raw payloads.  Settings are read
    # far more often than they change and several users may poll the same
    # ranges, so this cuts repeated serial-bus reads.
    _READ_CACHE_TTL = 30.0

    @staticmethod
    def _block_for(register: int) -> tuple:
        base = (register // ModbusController._HOLDING_BLOCK_COUNT) * ModbusController._HOLDING_BLOCK_COUNT
        count = ModbusController._HOLDING_BLOCK_COUNT
        return base, count, register - base

    @staticmethod
    def _coalesce_ranges(registers, max_count: int) -> list:
        """Merge register addresses into the fewest contiguous read requests.

        Holding blocks tile the register space, so adjacent needed blocks are
        merged into one request.  Because the merged range only spans blocks that
        would be read anyway, no extra registers are fetched.  Runs longer than
        ``max_count`` are split into separate requests.
        """
        block = ModbusController._HOLDING_BLOCK_COUNT
        bases = sorted({(reg // block) * block for reg in registers})
        ranges = []
        for base in bases:
            if ranges:
                start, count = ranges[-1]
                if base == start + count and count + block <= max_count:
                    ranges[-1] = (start, count + block)
                    continue
            ranges.append((base, min(block, max_count)))
        return ranges

    async def _fetch_holding_range(self, start: int, count: int) -> bytes:
        """Perform the actual serial read of ``count`` holding registers."""
        frame = modbus_service.build_read_holding_request(
            self._dongle_serial, self._inverter_serial, register=start, count=count
        )
        raw = await self.execute_raw(frame, FN_READ_HOLDING)
        register, payload = modbus_service.read_response_values(raw, FN_READ_HOLDING)
        logger.debug(
            "Read holding range reg=%s count=%s: echo_reg=%s payload=%d bytes",
            start, count, register, len(payload),
        )
        return payload

    def _read_cache_get(self, key: tuple) -> bytes:
        with self._read_cache_lock:
            entry = self._read_cache.get(key)
            if entry is None:
                return None
            payload, expiry = entry
            if time.monotonic() >= expiry:
                del self._read_cache[key]
                return None
            return payload

    def _read_cache_set(self, key: tuple, payload: bytes) -> None:
        expiry = time.monotonic() + self._read_cache_ttl
        with self._read_cache_lock:
            self._read_cache[key] = (payload, expiry)

    def _update_register_cache(self, dongle: str, inverter: str, register: int, raw: int) -> None:
        """Patch a freshly written register into cached payloads so follow-up
        reads do not force a cold serial re-read.  Entries that cannot be
        patched are dropped (the next read refreshes them).
        """
        with self._read_cache_lock:
            for key in list(self._read_cache):
                if not (
                    key[0] == dongle and key[1] == inverter
                    and key[2] <= register < key[2] + key[3]
                ):
                    continue
                payload, expiry = self._read_cache[key]
                offset = (register - key[2]) * 2
                if offset + 2 > len(payload):
                    del self._read_cache[key]
                    continue
                patched = payload[:offset] + raw.to_bytes(2, "little") + payload[offset + 2:]
                self._read_cache[key] = (patched, expiry)

    async def _read_holding_range_async(self, start: int, count: int,
                                        use_cache: bool = True) -> bytes:
        """Fetch a holding range, served from the TTL cache when fresh."""
        key = (self._dongle_serial, self._inverter_serial, start, count)
        if use_cache:
            payload = self._read_cache_get(key)
            if payload is not None:
                logger.debug("Holding read cache hit reg=%s count=%s", start, count)
                return payload
        payload = await self._fetch_holding_range(start, count)
        self._read_cache_set(key, payload)
        return payload

    async def _read_holding_block_async(self, base: int, count: int,
                                        use_cache: bool = True) -> bytes:
        return await self._read_holding_range_async(base, count, use_cache=use_cache)

    async def _read_holding_async(self, register: int, use_cache: bool = True) -> int:
        base, count, offset = self._block_for(register)
        payload = await self._read_holding_block_async(base, count, use_cache=use_cache)
        start = offset * 2
        if start + 2 > len(payload):
            raise modbus_service.ModbusTruncatedFrame(
                "Holding block %s:%s too short (%d bytes) for register %s"
                % (base, count, len(payload), register)
            )
        return modbus_service.to_int(payload[start:start + 2])

    async def read_items(self, keys) -> dict:
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

        max_count = max(self._HOLDING_MAX_COUNT, self._HOLDING_BLOCK_COUNT)
        ranges = self._coalesce_ranges([item["reg"] for item in items], max_count)
        logger.debug(
            "Read %d register items in %d request(s): %s",
            len(items),
            len(ranges),
            ranges,
        )

        # Read each needed range once, then build a reg→raw map.  payload[2*k]
        # is the register at ``start + k``.
        reg_raw: dict[int, int] = {}
        for start, count in ranges:
            payload = await self._read_holding_range_async(start, count)
            for item in items:
                reg = item["reg"]
                if not (start <= reg < start + count):
                    continue
                offset = (reg - start) * 2
                if offset + 2 <= len(payload):
                    reg_raw[reg] = modbus_service.to_int(payload[offset : offset + 2])
                else:
                    logger.warning(
                        "Register %s out of range %s:%s (payload_len=%s)",
                        reg, start, count, len(payload),
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
            current_raw = await self._read_holding_async(item["reg"])
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
        # execute() parses and returns the device's 0x06 echo of the written
        # register value, which confirms the write landed without an extra read.
        echo_raw = await self.execute(frame, FN_WRITE_SINGLE)

        if item.get("verify"):
            # Items that explicitly require strong confirmation re-read the
            # register after the write instead of trusting the echo.
            fresh_raw = await self._read_holding_async(item["reg"], use_cache=False)
        else:
            fresh_raw = echo_raw
            # Keep the read cache warm with the confirmed echo value so other
            # users do not force a cold re-read after this write.
            self._update_register_cache(
                self._dongle_serial, self._inverter_serial, item["reg"], echo_raw
            )
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