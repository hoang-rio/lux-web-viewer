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
        self._require_available()
        if self.mode == MODE_SERVER:
            return await self._server_execute(frame, expected_fn)
        return await asyncio.to_thread(self._blocking_execute, frame, expected_fn)

    def _blocking_execute(self, frame: bytes, expected_fn: int) -> int:
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
                logger.warning("Modbus %s: timeout waiting for reply", expected_fn)
                data = data or b""
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
        if not data:
            raise ModbusTimeoutError("No Modbus response from dongle")
        return modbus_service.parse_response(data, expected_fn)

    async def _server_execute(self, frame: bytes, expected_fn: int) -> int:
        if self._server is None or self._server_loop is None:
            raise ModbusUnavailableError("Server transport not initialized")
        future = asyncio.run_coroutine_threadsafe(
            self._server.request_modbus(bytes(frame), expected_fn),
            self._server_loop,
        )
        return await asyncio.wrap_future(future)

    def read_holding(self, register: int) -> int:
        """Blocking read of a single holding register (used internally)."""
        return self._blocking_execute(
            modbus_service.build_read_holding_request(
                self._dongle_serial, self._inverter_serial, register, count=1
            ),
            FN_READ_HOLDING,
        )

    async def read_holding_async(self, register: int) -> int:
        frame = modbus_service.build_read_holding_request(
            self._dongle_serial, self._inverter_serial, register, count=1
        )
        return await self.execute(frame, FN_READ_HOLDING)

    async def read_items(self, keys) -> dict:
        """Read multiple catalog items, deduplicating shared registers."""
        items = []
        seen = set()
        for key in keys:
            item = modbus_registers.get_item(key)
            if item is None or item["reg"] in seen:
                continue
            seen.add(item["reg"])
            items.append(item)

        raws = {}
        for item in items:
            raws[item["reg"]] = await self.read_holding_async(item["reg"])

        result = {}
        for key in keys:
            item = modbus_registers.get_item(key)
            if item is None:
                result[key] = None
                continue
            raw = raws.get(item["reg"])
            if raw is None:
                result[key] = None
                continue
            result[key] = modbus_registers.extract_value(item, raw)
        return result

    async def write_item(self, item: dict, value, confirm: bool = True) -> object:
        """Validate and write a catalog item, returning the new display value."""
        if item.get("danger") and not confirm:
            raise ValueError("Confirmation is required for danger item %s" % item["key"])

        raw_part = modbus_registers.encode_value(item, value)

        shift, mask = modbus_registers.mask_for(item)
        needs_rmw = mask != 0xFFFF

        if needs_rmw:
            current_raw = await self.read_holding_async(item["reg"])
            raw = modbus_registers.merge_raw(item, current_raw, raw_part)
        else:
            raw = raw_part

        frame = modbus_service.build_write_single_request(
            self._dongle_serial, self._inverter_serial, item["reg"], raw
        )
        await self.execute(frame, FN_WRITE_SINGLE)

        fresh_raw = await self.read_holding_async(item["reg"])
        return modbus_registers.extract_value(item, fresh_raw)


controller = ModbusController()


def configure(config: dict) -> None:
    controller.configure(config)


def set_server(server, main_loop) -> None:
    controller.set_server(server, main_loop)