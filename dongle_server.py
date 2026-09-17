import asyncio
import logging
from datetime import datetime
from typing import Optional
import dongle_handler
import modbus_service

SERVER_REQUEST_FUNCTIONS = (0x03, 0x04, 0x06, 0x10)


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
        self.__inverter_data: Optional[dict] = None
        self.__data_received_event = asyncio.Event()
        self.__read_count = 0
        self.__cached_data: dict = {}
        self.__modbus_pending: list = []
        self.__modbus_wake = asyncio.Event()
        # The real dongle services one command at a time and silently drops any
        # command that arrives while it is still busy with the previous one
        # (~0.6s of processing per command).  Keep every dongle write spaced by
        # at least this gap so a Modbus request is never dropped because the
        # poll loop fired it too soon after a ReadInput request.
        self.__last_dongle_send = 0.0
        self.__dongle_command_gap = float(config.get("DONGLE_COMMAND_GAP", 0.8))

    async def __write_dongle_command(self, writer: asyncio.StreamWriter, raw: bytes) -> None:
        loop = asyncio.get_running_loop()
        elapsed = loop.time() - self.__last_dongle_send
        if elapsed < self.__dongle_command_gap:
            await asyncio.sleep(self.__dongle_command_gap - elapsed)
        writer.write(raw)
        await writer.drain()
        self.__last_dongle_send = loop.time()

    async def request_modbus(self, frame: bytes, expected_fn: int, timeout: float = 6.0, return_raw: bool = False):
        """Send a Modbus request on the active dongle connection (if any).

        The request is interleaved with the polling loop. Only a single Modbus
        exchange is in flight at a time; callers are awaited until the matching
        reply arrives (or ``timeout`` elapses). Returns the parsed response
        value unless ``return_raw`` is set, in which case the raw reply frame
        bytes are returned.
        """
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
        }
        self.__modbus_pending.append(entry)
        self.__modbus_wake.set()
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            for i, item in enumerate(self.__modbus_pending):
                if item.get("future") is future:
                    del self.__modbus_pending[i]
                    break
            raise

    def __has_unsent_modbus(self) -> bool:
        return any(not item.get("sent") for item in self.__modbus_pending)

    def __has_inflight_modbus(self) -> bool:
        return any(item.get("sent") for item in self.__modbus_pending)

    async def __send_pending_modbus(self, writer: asyncio.StreamWriter) -> bool:
        """Send the next queued Modbus request, if any. Returns True when sent."""
        for item in self.__modbus_pending:
            if not item.get("sent"):
                item["sent"] = True
                await self.__write_dongle_command(writer, item["frame"])
                self.__logger.debug("Sent Modbus request (fn=0x%02x) to dongle", item["fn"])
                return True
        return False

    def __resolve_modbus(self, raw_data) -> bool:
        """Try to match raw data against a pending Modbus request; resolve on match."""
        if not self.__modbus_pending:
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

        for i, item in enumerate(self.__modbus_pending):
            if item["fn"] != base_fn:
                continue
            if item.get("register") is not None and item["register"] != register:
                continue
            entry = self.__modbus_pending.pop(i)
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

    async def __read_with_wake(self, reader: asyncio.StreamReader, timeout: float, buffer: bytearray) -> Optional[list]:
        """Read a complete frame from the dongle, aborting early on a Modbus wake.

        ``buffer`` accumulates bytes across calls so that frames arriving
        concatenated in a single TCP segment are still processed one at a time.

        Returns a list of bytes when a complete frame is available, an empty
        list when interrupted by a Modbus wake before a frame arrived, b"" when
        the dongle disconnected, and None on a full timeout.
        """
        while True:
            frame = self.__pop_complete_frame(buffer)
            if frame is not None:
                return list(frame)
            self.__modbus_wake.clear()
            # A Modbus request queued *before* the wake was cleared must not be
            # lost: return immediately so the loop top sends it right away.
            if self.__has_unsent_modbus():
                return []
            read_task = asyncio.ensure_future(reader.read(1024))
            wake_task = asyncio.ensure_future(self.__modbus_wake.wait())
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
                buffer += chunk
                frame = self.__pop_complete_frame(buffer)
                if frame is not None:
                    return list(frame)
                timeout = min(timeout, 1.0) if timeout else 0.5
                continue
            if self.__has_unsent_modbus():
                frame = self.__pop_complete_frame(buffer)
                return list(frame) if frame is not None else []
            return None

    async def __interruptible_sleep(self, seconds: float) -> None:
        self.__modbus_wake.clear()
        # A Modbus request queued before the wake was cleared must not be lost.
        if self.__has_unsent_modbus():
            return
        try:
            await asyncio.wait_for(self.__modbus_wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

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

    async def __handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ):
        """Handle incoming client connection from dongle."""
        client_addr = writer.get_extra_info('peername')
        self.__logger.info("Dongle connected from %s", client_addr)

        try:
            dongle_serial = self.__config.get("DONGLE_SERIAL", "")
            inverter_serial = self.__config.get("INVERT_SERIAL", "")
            sleep_time = int(self.__config.get("SLEEP_TIME", 120))
            read_input_mode_str = self.__config.get("READ_INPUT_MODE", dongle_handler.READ_INPUT_MODE_ALL)
            read_mode = dongle_handler.normalize_read_input_mode(read_input_mode_str)
            
            def get_current_plan():
                self.__read_count += 1
                interval = int(self.__config.get("READ_LOW_FREQ_INTERVAL") or 1)
                # Read on the first time (count=1), every 'interval' times, or if cache is empty
                should_read_low_freq = interval <= 1 or (self.__read_count % interval == 1) or not self.__cached_data
                return dongle_handler.get_read_input_registers(read_input_mode_str, should_read_low_freq)

            registers = get_current_plan()
            next_register_idx = 0
            all_mode_received_registers: set[int] = set()

            def build_poll_request(register: int) -> bytes:
                return dongle_handler.Dongle.build_read_input_request(
                    dongle_serial,
                    inverter_serial,
                    register=register,
                    protocol=1,
                )

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
            
            if dongle_serial and inverter_serial:
                self.__logger.debug(
                    "DONGLE_SERIAL and INVERT_SERIAL configured, "
                    "will send ReadInput requests to dongle",
                )
                # Send ReadInput request immediately when dongle connects
                current_register = get_next_register()
                request = build_poll_request(current_register)
                await self.__write_dongle_command(writer, request)
                self.__logger.debug(
                    "Sent ReadInput request (register=%s, protocol 1) to %s immediately",
                    current_register,
                    client_addr,
                )
                advance_next_register()
            else:
                self.__logger.warning(
                    "DONGLE_SERIAL or INVERT_SERIAL not configured, "
                    "waiting for dongle to send data"
                )

            read_buffer = bytearray()

            while True:
                try:
                    # Send a queued Modbus request when dongle data is quiet, so its
                    # reply is read back before the next ReadInput poll cycle.  While a
                    # Modbus request is queued or already in flight, keep the read window
                    # short regardless of the poll timeout: the reply must be consumed
                    # before the caller's own timeout, and interleaved ReadInput replies
                    # must not delay it by a whole polling sleep.
                    if await self.__send_pending_modbus(writer) or self.__has_inflight_modbus():
                        data = await self.__read_with_wake(reader, 1.5, read_buffer)
                    else:
                        data = await self.__read_with_wake(
                            reader,
                            int(self.__config.get("SERVER_MODE_TIMEOUT", 300)),
                            read_buffer,
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
                    if self.__resolve_modbus(raw_data):
                        # A Modbus reply was consumed; keep waiting for its data next.
                        if not await self.__send_pending_modbus(writer):
                            # Send next ReadInput request after a Modbus exchange
                            if dongle_serial and inverter_serial:
                                current_register = get_next_register()
                                request = build_poll_request(current_register)
                                await self.__write_dongle_command(writer, request)
                                self.__logger.debug(
                                    "Sent ReadInput request (register=%s) to %s after modbus",
                                    current_register,
                                    client_addr,
                                )
                                advance_next_register()
                            await self.__interruptible_sleep(sleep_time)
                        continue

                    parsed_data = self.__parse_inverter_data(raw_data)
                    if parsed_data is not None:
                        self.__cached_data.update(parsed_data)
                        if read_mode == dongle_handler.READ_INPUT_MODE_INPUT1_ONLY:
                            self.__inverter_data = dict(self.__cached_data)
                            self.__data_received_event.set()
                            self.__logger.info(
                                "ReadInput complete from %s (soc=%s%%, p_pv=%sW)",
                                client_addr,
                                parsed_data.get("soc", "?"),
                                parsed_data.get("p_pv", "?"),
                            )
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
                                    self.__inverter_data = dict(self.__cached_data)
                                    self.__inverter_data['deviceTime'] = datetime.now().strftime(
                                        "%Y-%m-%d %H:%M:%S"
                                    )
                                    self.__data_received_event.set()
                                    self.__logger.info(
                                        "ReadInput complete (all) from %s (soc=%s%%, p_pv=%sW)",
                                        client_addr,
                                        self.__inverter_data.get("soc", "?"),
                                        self.__inverter_data.get("p_pv", "?"),
                                    )
                                    all_mode_received_registers.clear()
                                    # Update plan for next cycle
                                    registers = get_current_plan()
                                    next_register_idx = 0

                    # Prefer sending queued Modbus requests over the next poll.
                    if await self.__send_pending_modbus(writer):
                        continue

                    # A Modbus reply is still in flight: keep draining frames
                    # (short read window at the top of the loop) instead of
                    # starting a new ReadInput poll + sleep cycle, so the reply
                    # reaches its caller before their timeout expires.
                    if self.__has_inflight_modbus():
                        continue

                    # Send next ReadInput request after processing
                    if dongle_serial and inverter_serial:
                        current_register = get_next_register()
                        request = build_poll_request(current_register)
                        await self.__write_dongle_command(writer, request)
                        self.__logger.debug(
                            "Sent ReadInput request (register=%s) to %s",
                            current_register,
                            client_addr,
                        )
                        advance_next_register()

                    # Wait for next polling interval (interruptible by Modbus)
                    await self.__interruptible_sleep(sleep_time)

                except asyncio.TimeoutError:
                    self.__logger.warning(
                        "Timeout waiting for data from %s",
                        client_addr
                    )
                    # Keep polling on timeout in case the previous response was dropped.
                    if await self.__send_pending_modbus(writer):
                        continue
                    # A Modbus reply is in flight: loop again with the short read
                    # window rather than dropping into a polling sleep.
                    if self.__has_inflight_modbus():
                        continue
                    if dongle_serial and inverter_serial:
                        current_register = get_next_register()
                        request = build_poll_request(current_register)
                        await self.__write_dongle_command(writer, request)
                        self.__logger.debug(
                            "Resent ReadInput request (register=%s) to %s after timeout",
                            current_register,
                            client_addr,
                        )
                        advance_next_register()
                    await self.__interruptible_sleep(sleep_time)
                except Exception as e:
                    self.__logger.exception(
                        "Error handling data from %s: %s",
                        client_addr,
                        e
                    )
                    break

        finally:
            writer.close()
            await writer.wait_closed()
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

    async def wait_for_data(
        self,
        timeout: Optional[float] = None
    ) -> Optional[dict]:
        """Wait for new data from dongle with optional timeout."""
        try:
            self.__data_received_event.clear()
            await asyncio.wait_for(
                self.__data_received_event.wait(),
                timeout=timeout
            )
            data = self.__inverter_data
            self.__inverter_data = None
            return data
        except asyncio.TimeoutError:
            return None

    async def stop_server(self):
        """Stop the TCP server."""
        if self.__server is not None:
            self.__server.close()
            await self.__server.wait_closed()
            self.__logger.info("Dongle server stopped")