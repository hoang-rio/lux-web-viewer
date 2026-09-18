import asyncio
import logging
import time
from datetime import datetime
from typing import Optional
import dongle_handler
import modbus_service
from sleep_cache import (
    get_cached_sleep_time,
    normalize_sleep_time as _normalize_sleep_time,
    set_cached_sleep_time,
)

# Function codes that can arrive on a server connection and must be routed to a
# pending Modbus request (ReadHolding/ReadInput reads + writes).
SERVER_REQUEST_FUNCTIONS = (0x03, 0x04, 0x06, 0x10)
# Read function codes are idempotent, so a request that times out can be
# re-sent once safely (writes 0x06/0x10 are not retried).
IDEMPOTENT_READ_FUNCTIONS = (0x03, 0x04)


def _resolve_inverter_id(dongle_serial: str, logger: logging.Logger) -> Optional[str]:
    """Look up the PostgreSQL inverter UUID for the given dongle serial.

    Returns the inverter UUID string when found, or None when PostgreSQL is not
    configured or the dongle is not registered.
    """
    try:
        from multi_tenant.db import get_db_session
        from multi_tenant import repository as repo
        session = next(get_db_session())
        try:
            inverter = repo.get_inverter_by_dongle_serial(session, dongle_serial)
            if inverter:
                return str(inverter.id)
        finally:
            session.close()
    except RuntimeError:
        # POSTGRES_DB_URL not configured — multi-tenant mode disabled
        pass
    except Exception as exc:
        logger.warning("Failed to resolve inverter_id for dongle_serial=%s: %s", dongle_serial, exc)
    return None


def _resolve_sleep_time(dongle_serial: str, default_sleep_time: int, logger: logging.Logger) -> int:
    if not dongle_serial:
        return default_sleep_time
    try:
        from multi_tenant.db import get_db_session
        from multi_tenant import repository as repo
        session = next(get_db_session())
        try:
            inverter = repo.get_inverter_by_dongle_serial(session, dongle_serial)
            if inverter is None:
                logger.debug("No inverter found for dongle_serial=%s; using default sleep_time=%s", dongle_serial, default_sleep_time)
                return default_sleep_time

            user_id_str = str(inverter.user_id)
            # Check cache first
            cached_value = get_cached_sleep_time(user_id_str)
            if cached_value is not None:
                logger.debug("SLEEP_TIME cache hit for user_id=%s -> %s (dongle_serial=%s)", user_id_str, cached_value, dongle_serial)
                return cached_value

            # Query and cache
            user_sleep_time = repo.get_user_setting(session, inverter.user_id, "SLEEP_TIME")
            normalized = set_cached_sleep_time(user_id_str, user_sleep_time, default_sleep_time)
            logger.info("Resolved per-user SLEEP_TIME=%s for user_id=%s (dongle_serial=%s)", normalized, user_id_str, dongle_serial)
            return normalized
        finally:
            session.close()
    except RuntimeError:
        return default_sleep_time
    except Exception as exc:
        logger.warning("Failed to resolve sleep_time for dongle_serial=%s: %s", dongle_serial, exc)
        return default_sleep_time


class _DongleConnection:
    """Per-connection state: identity, read buffer, Modbus queues, write pacing."""

    def __init__(self, dongle_serial: str, inverter_serial: str) -> None:
        self.dongle_serial = dongle_serial
        self.inverter_serial = inverter_serial
        self.read_buffer = bytearray()
        self.modbus_pending: list = []
        self.modbus_wake = asyncio.Event()
        # The real dongle services one command at a time and silently drops any
        # command that arrives while it is still busy with the previous one
        # (~0.6s of processing per command).  Keep every dongle write spaced by
        # this gap so a Modbus request is never dropped because the poll loop
        # fired it too soon after a ReadInput request.
        self.last_dongle_send = 0.0
        # Duplicate-send guard: track last sent register/time and skip sends
        # that happen within a very short interval (likely accidental
        # duplicate). Guarding avoids duplicate write/drain cycles while
        # preserving normal polling behavior.
        self.last_sent_register: int | None = None
        self.last_sent_time: float = 0.0
        self.last_send_guard_seconds = 0.5


class DongleServer:
    __server: asyncio.Server | None = None
    __config: dict
    __logger: logging.Logger
    __port: int
    __host: str

    def __init__(self, logger: logging.Logger, config: dict) -> None:
        self.__config = config
        self.__logger = logger
        self.__host = config.get("SERVER_MODE_HOST", "0.0.0.0")
        self.__port = int(config.get("SERVER_MODE_PORT", 4346))
        self.__data_queue: asyncio.Queue[dict] = asyncio.Queue()
        # Registered connections keyed by resolved dongle serial, so Modbus
        # requests can be routed to a specific dongle in multi-tenant mode.
        self.__connections: dict[str, _DongleConnection] = {}
        self.__dongle_command_gap = float(config.get("DONGLE_COMMAND_GAP", 0.8))
        # On-demand Modbus bus-load metrics (measure serial contention between
        # the continuous ReadInput polls and settings reads/writes).
        self._modbus_stats = {
            "enqueued": 0,
            "sent": 0,
            "replied": 0,
            "timed_out": 0,
            "retried": 0,
            "queue_wait_total": 0.0,
            "queue_wait_max": 0.0,
            "latency_total": 0.0,
            "latency_max": 0.0,
        }

    async def __write_dongle_command(self, writer: asyncio.StreamWriter, raw: bytes, ctx: _DongleConnection) -> None:
        loop = asyncio.get_running_loop()
        elapsed = loop.time() - ctx.last_dongle_send
        if elapsed < self.__dongle_command_gap:
            await asyncio.sleep(self.__dongle_command_gap - elapsed)
        writer.write(raw)
        await writer.drain()
        ctx.last_dongle_send = loop.time()

    # --- connection registry -------------------------------------------------

    def __register_connection(self, ctx: _DongleConnection) -> None:
        if ctx.dongle_serial:
            self.__connections[ctx.dongle_serial] = ctx

    def __reregister_connection(self, ctx: _DongleConnection, new_serial: str) -> None:
        new_serial = (new_serial or "").strip()
        if not new_serial or new_serial == ctx.dongle_serial:
            return
        old = ctx.dongle_serial
        if old and self.__connections.get(old) is ctx:
            del self.__connections[old]
        ctx.dongle_serial = new_serial
        self.__connections[new_serial] = ctx

    def __unregister_connection(self, ctx: _DongleConnection) -> None:
        if ctx.dongle_serial and self.__connections.get(ctx.dongle_serial) is ctx:
            del self.__connections[ctx.dongle_serial]
        for entry in ctx.modbus_pending:
            future = entry.get("future")
            if future is not None and not future.done():
                future.set_exception(
                    modbus_service.ModbusError("Dongle disconnected while Modbus request pending")
                )
        ctx.modbus_pending.clear()

    def __connection_for(self, dongle_serial=None) -> Optional[_DongleConnection]:
        """Resolve a connection for ``dongle_serial``.

        With an explicit serial the matching registered connection is returned;
        without one, the sole registered connection is returned (None when there
        are zero or several).
        """
        serial = (dongle_serial or "").strip()
        if serial:
            return self.__connections.get(serial)
        if len(self.__connections) == 1:
            return next(iter(self.__connections.values()))
        return None

    def connection_serials(self, dongle_serial=None):
        """Return the (dongle_serial, inverter_serial) pair for a connection.

        Used by ModbusController to build request frames with the identity of
        the routed dongle (multi-tenant mode).  Returns None when no single
        connection can be resolved.
        """
        ctx = self.__connection_for(dongle_serial)
        if ctx is None:
            return None
        return ctx.dongle_serial, ctx.inverter_serial

    # --- Modbus interleave machinery ----------------------------------------

    @staticmethod
    def __dequeue_modbus(pending: list, future: asyncio.Future) -> None:
        for i, item in enumerate(pending):
            if item.get("future") is future:
                del pending[i]
                return

    async def request_modbus(self, frame: bytes, expected_fn: int, timeout: float = 6.0, return_raw: bool = False, dongle_serial=None):
        """Send a Modbus request on the matching dongle connection (if any).

        The request is interleaved with the polling loop of the target
        connection. Only a single Modbus exchange is in flight at a time;
        callers are awaited until the matching reply arrives (or ``timeout``
        elapses). Returns the parsed response value unless ``return_raw`` is
        set, in which case the raw reply frame bytes are returned.

        ``dongle_serial`` selects the target connection and is required when
        several dongles are connected. Read requests (0x03/0x04) are
        idempotent, so on a timeout the frame is re-sent once within the same
        overall ``timeout`` window — a single dropped request no longer fails a
        whole read.
        """
        ctx = self.__connection_for(dongle_serial)
        if ctx is None:
            if (dongle_serial or "").strip():
                raise modbus_service.ModbusError(
                    "No registered dongle connection for dongle_serial=%s" % dongle_serial
                )
            if len(self.__connections) > 1:
                raise modbus_service.ModbusError(
                    "Multiple dongle connections are active; dongle_serial is required"
                )
            raise modbus_service.ModbusError("No dongle connection for Modbus request")

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        try:
            register = modbus_service.request_register(frame)
        except modbus_service.ModbusError:
            register = None
        entry = {
            "frame": bytes(frame),
            "fn": expected_fn,
            "register": register,
            "future": future,
            "sent": False,
            "return_raw": bool(return_raw),
            "enqueued_at": time.monotonic(),
        }
        retryable = expected_fn in IDEMPOTENT_READ_FUNCTIONS
        attempt_timeout = timeout / 2 if retryable else timeout
        ctx.modbus_pending.append(entry)
        self._modbus_stats["enqueued"] += 1
        ctx.modbus_wake.set()
        try:
            return await asyncio.wait_for(asyncio.shield(future), attempt_timeout)
        except asyncio.TimeoutError:
            self.__dequeue_modbus(ctx.modbus_pending, future)
            self._modbus_stats["timed_out"] += 1
            if not retryable:
                raise
            self._modbus_stats["retried"] += 1
            self.__logger.warning(
                "Modbus fn=0x%02x reg=%s timed out on dongle %s; retrying once",
                expected_fn,
                register,
                ctx.dongle_serial,
            )
            future = loop.create_future()
            entry["future"] = future
            entry["sent"] = False
            ctx.modbus_pending.append(entry)
            ctx.modbus_wake.set()
            try:
                return await asyncio.wait_for(asyncio.shield(future), attempt_timeout)
            except asyncio.TimeoutError:
                self.__dequeue_modbus(ctx.modbus_pending, future)
                self._modbus_stats["timed_out"] += 1
                raise
        except asyncio.CancelledError:
            self.__dequeue_modbus(ctx.modbus_pending, future)
            raise

    def __has_unsent_modbus(self, ctx: _DongleConnection) -> bool:
        return any(not item.get("sent") for item in ctx.modbus_pending)

    def modbus_stats(self) -> dict:
        """Snapshot of on-demand Modbus bus-load metrics (serial contention)."""
        s = dict(self._modbus_stats)
        s["avg_queue_wait"] = s["queue_wait_total"] / (s["sent"] or 1)
        s["avg_latency"] = s["latency_total"] / (s["replied"] or 1)
        return s

    def __has_inflight_modbus(self, ctx: _DongleConnection) -> bool:
        return any(item.get("sent") for item in ctx.modbus_pending)

    async def __send_pending_modbus(self, writer: asyncio.StreamWriter, ctx: _DongleConnection) -> bool:
        """Send the next queued Modbus request, if any. Returns True when sent."""
        for item in ctx.modbus_pending:
            if not item.get("sent"):
                item["sent"] = True
                await self.__write_dongle_command(writer, item["frame"], ctx)
                item["sent_at"] = time.monotonic()
                self._modbus_stats["sent"] += 1
                waited = item["sent_at"] - item.get("enqueued_at", item["sent_at"])
                self._modbus_stats["queue_wait_total"] += waited
                if waited > self._modbus_stats["queue_wait_max"]:
                    self._modbus_stats["queue_wait_max"] = waited
                self.__logger.debug(
                    "Sent Modbus request (fn=0x%02x reg=%s) to dongle %s",
                    item["fn"], item.get("register"), ctx.dongle_serial,
                )
                return True
        return False

    def __resolve_modbus(self, raw_data, ctx: _DongleConnection) -> bool:
        """Try to match raw data against a pending Modbus request; resolve on match."""
        if not ctx.modbus_pending:
            return False
        try:
            if len(raw_data) < 20:
                return False
            fn = modbus_service.response_function(raw_data)
            base_fn = fn & 0x7F
            if base_fn not in SERVER_REQUEST_FUNCTIONS:
                return False
            register = modbus_service.response_register(raw_data)
        except modbus_service.ModbusError:
            return False

        for i, item in enumerate(ctx.modbus_pending):
            if item["fn"] != base_fn:
                continue
            if item.get("register") is not None and item["register"] != register:
                continue
            entry = ctx.modbus_pending.pop(i)
            future = entry["future"]
            if future.done():
                return True
            try:
                if entry.get("return_raw"):
                    future.set_result(raw_data)
                else:
                    value = modbus_service.parse_response(raw_data, base_fn)
                    future.set_result(value)
            except Exception as e:
                future.set_exception(e)
            self._modbus_stats["replied"] += 1
            enqueued_at = entry.get("enqueued_at", time.monotonic())
            latency = time.monotonic() - enqueued_at
            self._modbus_stats["latency_total"] += latency
            if latency > self._modbus_stats["latency_max"]:
                self._modbus_stats["latency_max"] = latency
            queue_wait = (entry.get("sent_at") or enqueued_at) - enqueued_at
            warn_sec = float(self.__config.get("MODBUS_LATENCY_WARN_SEC") or 2.0)
            if latency >= warn_sec:
                self.__logger.warning(
                    "Modbus fn=0x%02x reg=%s latency=%.3fs (queue=%.3fs) exceeded %.1fs; "
                    "consider raising READ_LOW_FREQ_INTERVAL",
                    base_fn, register, latency, queue_wait, warn_sec,
                )
            else:
                self.__logger.debug(
                    "Modbus fn=0x%02x reg=%s latency=%.3fs (queue=%.3fs)",
                    base_fn, register, latency, queue_wait,
                )
            return True
        return False

    def __pop_complete_frame(self, buffer: bytearray) -> Optional[bytes]:
        """Pop one complete TCP frame from the read buffer, if available."""
        if len(buffer) < 8:
            return None
        total = modbus_service.to_int(buffer[4:6]) + modbus_service._FRAME_LENGTH_ADJUST
        if total < 20 or total > 4096:
            return None
        if len(buffer) < total:
            return None
        frame = bytes(buffer[:total])
        del buffer[:total]
        return frame

    @staticmethod
    def __resync_read_buffer(buffer: bytearray) -> None:
        """Drop leading bytes that cannot start a valid LXP frame.

        Mirrors the client-side resync (``modbus_controller``): a truncated
        frame left behind by a timeout must never permanently shift the parse
        point, or every later frame on the connection is read as garbage.
        """
        while len(buffer) >= 8:
            total = modbus_service.to_int(buffer[4:6]) + modbus_service._FRAME_LENGTH_ADJUST
            if total < 20 or total > 4096:
                del buffer[0]
                continue
            break

    async def __read_with_wake(self, reader: asyncio.StreamReader, timeout: float, ctx: _DongleConnection) -> Optional[list]:
        """Read a complete frame from the dongle, aborting early on a Modbus wake.

        ``ctx.read_buffer`` accumulates bytes across calls so that frames
        arriving concatenated in a single TCP segment are still processed one
        at a time.

        Returns a list of bytes when a complete frame is available, an empty
        list when interrupted by a Modbus wake before a frame arrived, b"" when
        the dongle disconnected, and None on a full timeout.
        """
        while True:
            self.__resync_read_buffer(ctx.read_buffer)
            frame = self.__pop_complete_frame(ctx.read_buffer)
            if frame is not None:
                return list(frame)
            ctx.modbus_wake.clear()
            # A Modbus request queued *before* the wake was cleared must not be
            # lost: return immediately so the loop top sends it right away.
            if self.__has_unsent_modbus(ctx):
                return []
            read_task = asyncio.ensure_future(reader.read(1024))
            wake_task = asyncio.ensure_future(ctx.modbus_wake.wait())
            try:
                done, _ = await asyncio.wait(
                    {read_task, wake_task},
                    timeout=timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for task in (read_task, wake_task):
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except (asyncio.CancelledError, Exception):
                            pass
            if read_task in done:
                chunk = read_task.result()
                if not chunk:
                    return b""
                ctx.read_buffer += chunk
                self.__resync_read_buffer(ctx.read_buffer)
                frame = self.__pop_complete_frame(ctx.read_buffer)
                if frame is not None:
                    return list(frame)
                timeout = min(timeout, 1.0) if timeout else 0.5
                continue
            if self.__has_unsent_modbus(ctx):
                frame = self.__pop_complete_frame(ctx.read_buffer)
                return list(frame) if frame is not None else []
            # Full timeout: any buffered bytes are a stale, incomplete frame the
            # dongle has already moved on from. Discard them so the next
            # complete frame parses cleanly for the rest of the connection.
            ctx.read_buffer.clear()
            return None

    async def __interruptible_sleep(self, seconds: float, ctx: _DongleConnection) -> None:
        ctx.modbus_wake.clear()
        # A Modbus request queued before the wake was cleared must not be lost.
        if self.__has_unsent_modbus(ctx):
            return
        try:
            await asyncio.wait_for(ctx.modbus_wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # --- dongle polling loop -------------------------------------------------

    async def start_server(self):
        """Start the TCP server to listen for dongle connections."""
        try:
            self.__server = await asyncio.start_server(
                self.__handle_client,
                self.__host,
                self.__port
            )
            self.__logger.info(
                "Dongle server started on %s:%s",
                self.__host,
                self.__port
            )
            async with self.__server:
                await self.__server.serve_forever()
        except Exception as e:
            self.__logger.exception("Failed to start dongle server: %s", e)
            raise

    async def __send_read_poll(
        self,
        writer: asyncio.StreamWriter,
        ctx: _DongleConnection,
        register: int,
        client_addr,
        verb: str,
    ) -> None:
        """Send the next ReadInput poll request with write pacing + duplicate guard."""
        dongle_serial = ctx.dongle_serial
        if not dongle_serial:
            return
        request = dongle_handler.Dongle.build_read_input_request(
            dongle_serial,
            ctx.inverter_serial,
            register=register,
            protocol=1,
        )
        now = time.time()
        if ctx.last_sent_register == register and now - ctx.last_sent_time < ctx.last_send_guard_seconds:
            self.__logger.info(
                "Skipping duplicate send for register=%s to %s (%s)",
                register,
                client_addr,
                verb,
            )
            return
        await self.__write_dongle_command(writer, request, ctx)
        self.__logger.info(
            "Sent ReadInput request (register=%s) to %s (%s)",
            register,
            client_addr,
            verb,
        )
        ctx.last_sent_register = register
        ctx.last_sent_time = now

    async def __handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ):
        """Handle incoming client connection from dongle."""
        client_addr = writer.get_extra_info('peername')
        self.__logger.info("Dongle connected from %s", client_addr)

        ctx = _DongleConnection(
            str(self.__config.get("DONGLE_SERIAL", "")),
            str(self.__config.get("INVERT_SERIAL", "")),
        )
        self.__register_connection(ctx)

        try:
            dongle_serial = ctx.dongle_serial
            inverter_serial = ctx.inverter_serial
            configured_sleep_time = _normalize_sleep_time(self.__config.get("SLEEP_TIME", 30))
            sleep_time = configured_sleep_time
            read_input_mode_str = self.__config.get("READ_INPUT_MODE", dongle_handler.READ_INPUT_MODE_ALL)
            read_mode = dongle_handler.normalize_read_input_mode(read_input_mode_str)

            read_count = 0
            cached_data: dict = {}

            def get_current_plan():
                nonlocal read_count
                read_count += 1
                interval = int(self.__config.get("READ_LOW_FREQ_INTERVAL") or 1)
                # Read on the first time (count=1), every 'interval' times, or if cache is empty
                should_read_low_freq = interval <= 1 or (read_count % interval == 1) or not cached_data
                return dongle_handler.get_read_input_registers(read_input_mode_str, should_read_low_freq)

            registers = get_current_plan()
            next_register_idx = 0
            all_mode_received_registers: set[int] = set()

            def extract_register(raw_data: list[int]) -> int | None:
                if len(raw_data) < 38:
                    return None
                body = raw_data[20: len(raw_data) - 2]
                if len(body) < 14:
                    return None
                return dongle_handler.Dongle.to_int(body[12:14])

            def get_next_register() -> int:
                return registers[next_register_idx % len(registers)]

            def advance_next_register():
                nonlocal next_register_idx
                next_register_idx = (next_register_idx + 1) % len(registers)

            if dongle_serial:
                self.__logger.debug(
                    "DONGLE_SERIAL configured (INVERT_SERIAL optional), "
                    "will send ReadInput requests to dongle",
                )
                # Send ReadInput request immediately when dongle connects
                current_register = get_next_register()
                await self.__send_read_poll(writer, ctx, current_register, client_addr, "immediate")
                advance_next_register()
            else:
                self.__logger.warning(
                    "DONGLE_SERIAL not configured, "
                    "waiting for dongle to send data"
                )

            while True:
                try:
                    # Send a queued Modbus request when dongle data is quiet, so its
                    # reply is read back before the next ReadInput poll cycle.  While a
                    # Modbus request is queued or already in flight, keep the read window
                    # short regardless of the poll timeout: the reply must be consumed
                    # before the caller's own timeout, and interleaved ReadInput replies
                    # must not delay it by a whole polling sleep.
                    if await self.__send_pending_modbus(writer, ctx) or self.__has_inflight_modbus(ctx):
                        data = await self.__read_with_wake(reader, 1.5, ctx)
                    else:
                        data = await self.__read_with_wake(
                            reader,
                            int(self.__config.get("SERVER_MODE_TIMEOUT", 300)),
                            ctx,
                        )

                    if data == []:
                        # Woken by a new Modbus request; loop again to send it.
                        continue
                    if data is None:
                        raise asyncio.TimeoutError()
                    if not data:
                        self.__logger.info(
                            "Dongle disconnected from %s",
                            client_addr
                        )
                        break

                    self.__logger.debug(
                        "Received %d bytes from %s (first 8: %s)",
                        len(data),
                        client_addr,
                        bytes(data[:8]).hex(),
                    )

                    # Parse the received data
                    raw_data = list(data)
                    if self.__resolve_modbus(raw_data, ctx):
                        # A Modbus reply was consumed; send any further queued
                        # requests, otherwise resume polling.
                        if not await self.__send_pending_modbus(writer, ctx):
                            if dongle_serial:
                                current_register = get_next_register()
                                await self.__send_read_poll(writer, ctx, current_register, client_addr, "after modbus")
                                advance_next_register()
                            await self.__interruptible_sleep(sleep_time, ctx)
                        continue

                    parsed_data = self.__parse_inverter_data(raw_data)
                    cycle_complete = False
                    if parsed_data is not None:
                        resolved_dongle_serial = str(parsed_data.get("dongle_serial") or dongle_serial).strip()
                        if resolved_dongle_serial:
                            self.__reregister_connection(ctx, resolved_dongle_serial)
                            dongle_serial = ctx.dongle_serial
                        if not inverter_serial:
                            parsed_inverter_serial = str(parsed_data.get("serial") or "").strip()
                            if parsed_inverter_serial:
                                inverter_serial = parsed_inverter_serial
                                ctx.inverter_serial = inverter_serial
                                self.__logger.info(
                                    "INVERT_SERIAL auto-updated from parsed dongle data: %s",
                                    inverter_serial,
                                )

                        sleep_time = _resolve_sleep_time(dongle_serial, configured_sleep_time, self.__logger)

                        cached_data.update(parsed_data)
                        if read_mode == dongle_handler.READ_INPUT_MODE_INPUT1_ONLY:
                            await self.__enqueue_inverter_data(dict(cached_data), client_addr)
                            self.__logger.info(
                                "ReadInput complete from %s (soc=%s%%, p_pv=%sW)",
                                client_addr,
                                parsed_data.get("soc", "?"),
                                parsed_data.get("p_pv", "?"),
                            )
                            cycle_complete = True
                            # Update plan for next cycle
                            registers = get_current_plan()
                            next_register_idx = 0
                        else:
                            register = extract_register(raw_data)
                            if register is not None:
                                all_mode_received_registers.add(register)
                                self.__logger.debug(
                                    "Parsed register=%s from %s (%s/%s)",
                                    register,
                                    client_addr,
                                    len(all_mode_received_registers),
                                    len(registers),
                                )
                                if all_mode_received_registers.issuperset(set(registers)):
                                    # Always update the timestamp to now for the returned data
                                    ready_data = dict(cached_data)
                                    ready_data['deviceTime'] = datetime.now().strftime(
                                        "%Y-%m-%d %H:%M:%S"
                                    )
                                    await self.__enqueue_inverter_data(ready_data, client_addr)
                                    self.__logger.info(
                                        "ReadInput complete (all) from %s (soc=%s%%, p_pv=%sW)",
                                        client_addr,
                                        ready_data.get("soc", "?"),
                                        ready_data.get("p_pv", "?"),
                                    )
                                    all_mode_received_registers.clear()
                                    cycle_complete = True
                                    # Update plan for next cycle
                                    registers = get_current_plan()
                                    next_register_idx = 0

                    # Prefer sending queued Modbus requests over the next poll.
                    if await self.__send_pending_modbus(writer, ctx):
                        continue

                    # A Modbus reply is still in flight: keep draining frames
                    # (short read window at the top of the loop) instead of
                    # starting a new ReadInput poll + sleep cycle, so the reply
                    # reaches its caller before their timeout expires.
                    if self.__has_inflight_modbus(ctx):
                        continue

                    # Send next ReadInput request after processing
                    if dongle_serial:
                        current_register = get_next_register()
                        await self.__send_read_poll(writer, ctx, current_register, client_addr, "after processing")
                        advance_next_register()

                    # Only sleep after a complete cycle; in ALL mode the intermediate
                    # register reads happen back-to-back without unnecessary delay.
                    # The sleep is interruptible by a new Modbus request.
                    if cycle_complete:
                        try:
                            source = "configured"
                            if sleep_time != configured_sleep_time:
                                # Try to resolve inverter id for helpful debugging context
                                try:
                                    inverter_id = _resolve_inverter_id(dongle_serial, self.__logger)
                                    source = f"user:{inverter_id}" if inverter_id else "user"
                                except Exception:
                                    source = "user"
                        except Exception:
                            source = "configured"
                        self.__logger.info(
                            "Waiting %s seconds before next request to %s (source: %s)",
                            sleep_time,
                            client_addr,
                            source,
                        )
                        await self.__interruptible_sleep(sleep_time, ctx)

                except asyncio.TimeoutError:
                    self.__logger.warning(
                        "Timeout waiting for data from %s",
                        client_addr
                    )
                    # Keep polling on timeout in case the previous response was dropped.
                    if await self.__send_pending_modbus(writer, ctx):
                        continue
                    # A Modbus reply is in flight: loop again with the short read
                    # window rather than dropping into a polling sleep.
                    if self.__has_inflight_modbus(ctx):
                        continue
                    if dongle_serial:
                        # Update plan (may force full poll if cache is empty)
                        registers = get_current_plan()
                        current_register = get_next_register()
                        await self.__send_read_poll(writer, ctx, current_register, client_addr, "after timeout")
                        advance_next_register()
                    await self.__interruptible_sleep(sleep_time, ctx)
                except Exception as e:
                    self.__logger.exception(
                        "Error handling data from %s: %s",
                        client_addr,
                        e
                    )
                    break

        finally:
            self.__unregister_connection(ctx)
            try:
                writer.close()
                await writer.wait_closed()
            except ConnectionResetError:
                # Remote peer reset connection while we were closing; ignore
                self.__logger.debug(
                    "Connection reset by peer while closing connection from %s",
                    client_addr
                )
            except Exception as e:
                self.__logger.debug(
                    "Exception while closing connection from %s: %s",
                    client_addr,
                    e
                )
            self.__logger.info("Connection from %s closed", client_addr)

    def __parse_inverter_data(self, data: list[int]) -> Optional[dict]:
        """Parse raw data from dongle - supports all ReadInput types (1-4 and All)."""
        try:
            # Validate basic TCP frame format
            if len(data) < 38:
                self.__logger.debug(
                    "Received data too short: %d bytes", len(data)
                )
                return None

            if data[0] == 0:
                self.__logger.debug(
                    "Received data starts with 0, skipping"
                )
                return None

            if data[7] != dongle_handler.TCP_FUNCTION_TRANSLATE:
                self.__logger.debug(
                    "Received data is not TranslatedData function: %s",
                    data[7] if len(data) > 7 else "N/A"
                )
                return None

            # Try to auto-detect and parse any ReadInput type
            parsed_data = dongle_handler.Dongle.read_input(data)

            if parsed_data is not None:
                # Add device timestamp
                parsed_data['deviceTime'] = datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

                # Log parsed type + a handful of key metrics specific to the register block type
                if parsed_data.get("input_type") == "all":
                    self.__logger.debug(
                        "Parsed ReadInputAll: soc=%s%% v_bat=%sV p_pv=%sW status=%s e_pv_all=%skWh t_inner=%s°C v_gen=%sV",
                        parsed_data.get("soc", "-"),
                        parsed_data.get("v_bat", "-"),
                        parsed_data.get("p_pv", "-"),
                        parsed_data.get("status_text", "-"),
                        parsed_data.get("e_pv_all", "-"),
                        parsed_data.get("t_inner", "-"),
                        parsed_data.get("v_gen", "-"),
                    )
                elif "soc" in parsed_data:
                    self.__logger.debug(
                        "Parsed ReadInput1: soc=%s%% v_bat=%sV p_pv=%sW status=%s",
                        parsed_data.get("soc", "-"),
                        parsed_data.get("v_bat", "-"),
                        parsed_data.get("p_pv", "-"),
                        parsed_data.get("status_text", "-"),
                    )
                elif "e_pv_all" in parsed_data:
                    self.__logger.debug(
                        "Parsed ReadInput2: e_pv_all=%skWh t_inner=%s°C runtime=%ss",
                        parsed_data.get("e_pv_all", "-"),
                        parsed_data.get("t_inner", "-"),
                        parsed_data.get("runtime", "-"),
                    )
                elif "bat_capacity" in parsed_data:
                    self.__logger.debug(
                        "Parsed ReadInput3: bat_capacity=%sAh bat_current=%sA cycle_count=%s vbat_inv=%sV",
                        parsed_data.get("bat_capacity", "-"),
                        parsed_data.get("bat_current", "-"),
                        parsed_data.get("cycle_count", "-"),
                        parsed_data.get("vbat_inv", "-"),
                    )
                elif "v_gen" in parsed_data:
                    self.__logger.debug(
                        "Parsed ReadInput4: v_gen=%sV p_gen=%sW p_eps_l1=%sW p_eps_l2=%sW",
                        parsed_data.get("v_gen", "-"),
                        parsed_data.get("p_gen", "-"),
                        parsed_data.get("p_eps_l1", "-"),
                        parsed_data.get("p_eps_l2", "-"),
                    )
                else:
                    self.__logger.debug(
                        "Parsed unknown ReadInput block: %s",
                        list(parsed_data.keys()),
                    )

                # Validate battery voltage if present
                if "v_bat" in parsed_data:
                    if parsed_data["v_bat"] < 40 or parsed_data["v_bat"] > 58:
                        self.__logger.warning(
                            "v_bat out of range: %.1fV (expected 40-58V)",
                            parsed_data["v_bat"],
                        )

                return parsed_data
            else:
                # Fallback: try ReadInput1 directly for backward compatibility
                data_len = len(data)
                register = None
                try:
                    body = data[20: len(data) - 2]
                    if len(body) >= 14:
                        register = dongle_handler.Dongle.to_int(body[12:14])
                except Exception:
                    register = None

                if data_len == 117 and register == 0:
                    parsed_data = dongle_handler.Dongle.read_input1(data)
                    if parsed_data is not None:
                        parsed_data['deviceTime'] = datetime.now().strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                        self.__logger.info(
                            "Parsed data using ReadInput1 fallback"
                        )
                        return parsed_data

                self.__logger.debug(
                    "Received data could not be parsed. "
                    "Length: %d, First byte: %s, Function: %s",
                    len(data),
                    data[0] if data else "N/A",
                    data[7] if len(data) > 7 else "N/A"
                )
                return None
        except Exception as e:
            self.__logger.exception("Failed to parse inverter data: %s", e)
            return None

    async def __enqueue_inverter_data(self, data: dict, client_addr) -> None:
        """Queue parsed inverter data so client handlers never overwrite each other.

        Tags ``_inverter_id`` in the dict when the dongle is registered in PostgreSQL.
        """
        dongle_serial = data.get("dongle_serial") or data.get("serial", "")
        if dongle_serial:
            inverter_id = _resolve_inverter_id(dongle_serial, self.__logger)
            if inverter_id:
                data = dict(data)  # avoid mutating the connection-local data
                data["_inverter_id"] = inverter_id
                self.__logger.debug(
                    "Resolved inverter_id=%s for dongle_serial=%s", inverter_id, dongle_serial
                )
        await self.__data_queue.put(data)
        self.__logger.debug(
            "Queued inverter data from %s. Pending queue size: %s",
            client_addr,
            self.__data_queue.qsize(),
        )

    async def wait_for_data(
        self,
        timeout: Optional[float] = None
    ) -> Optional[dict]:
        """Wait for next queued dongle payload with optional timeout."""
        try:
            return await asyncio.wait_for(self.__data_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def get_pending_data(self) -> Optional[dict]:
        """Return queued payload immediately without blocking."""
        try:
            return self.__data_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def stop_server(self):
        """Stop the TCP server."""
        if self.__server is not None:
            self.__server.close()
            await self.__server.wait_closed()
            self.__logger.info("Dongle server stopped")