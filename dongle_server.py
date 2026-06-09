import asyncio
import logging
from datetime import datetime
from typing import Optional
import dongle_handler
import time
from sleep_cache import (
    get_cached_sleep_time,
    normalize_sleep_time as _normalize_sleep_time,
    set_cached_sleep_time,
)


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

            # Duplicate-send guard: track last sent register/time and skip sends that happen
            # within a very short interval (likely accidental duplicate). Guarding avoids
            # duplicate write/drain cycles while preserving normal polling behavior.
            last_sent_register: int | None = None
            last_sent_time: float = 0.0
            last_send_guard_seconds = 0.5

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
            
            if dongle_serial:
                self.__logger.debug(
                    "DONGLE_SERIAL configured (INVERT_SERIAL optional), "
                    "will send ReadInput requests to dongle",
                )
                # Send ReadInput request immediately when dongle connects
                current_register = get_next_register()
                request = build_poll_request(current_register)
                now = time.time()
                if last_sent_register == current_register and now - last_sent_time < last_send_guard_seconds:
                    self.__logger.info(
                        "Skipping duplicate immediate send for register=%s to %s",
                        current_register,
                        client_addr,
                    )
                else:
                    writer.write(request)
                    await writer.drain()
                    self.__logger.info(
                        "Sent ReadInput request (register=%s, protocol 1) to %s (immediate)",
                        current_register,
                        client_addr,
                    )
                    last_sent_register = current_register
                    last_sent_time = now
                advance_next_register()
            else:
                self.__logger.warning(
                    "DONGLE_SERIAL not configured, "
                    "waiting for dongle to send data"
                )
            
            while True:
                try:
                    # Wait for data from dongle
                    try:
                        data = await asyncio.wait_for(
                            reader.read(1024),
                            timeout=int(self.__config.get("SERVER_MODE_TIMEOUT", 300))
                        )
                    except (ConnectionResetError, ConnectionAbortedError) as e:
                        # Remote peer reset/aborted connection while we were waiting for data
                        self.__logger.info(
                            "Connection reset by peer while reading from %s: %s",
                            client_addr,
                            e
                        )
                        break

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
                        data[:8].hex(),
                    )

                    # Parse the received data
                    raw_data = list(data)
                    parsed_data = self.__parse_inverter_data(raw_data)
                    cycle_complete = False
                    if parsed_data is not None:
                        resolved_dongle_serial = str(parsed_data.get("dongle_serial") or dongle_serial)
                        if not inverter_serial:
                            parsed_inverter_serial = str(parsed_data.get("serial") or "").strip()
                            if parsed_inverter_serial:
                                inverter_serial = parsed_inverter_serial
                                self.__logger.info(
                                    "INVERT_SERIAL auto-updated from parsed dongle data: %s",
                                    inverter_serial,
                                )

                        sleep_time = _resolve_sleep_time(resolved_dongle_serial, configured_sleep_time, self.__logger)
                        
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

                    # Send next ReadInput request after processing
                    if dongle_serial:
                        current_register = get_next_register()
                        request = build_poll_request(current_register)
                        now = time.time()
                        if last_sent_register == current_register and now - last_sent_time < last_send_guard_seconds:
                            self.__logger.info(
                                "Skipping duplicate send for register=%s to %s (recently sent)",
                                current_register,
                                client_addr,
                            )
                        else:
                            writer.write(request)
                            await writer.drain()
                            self.__logger.info(
                                "Sent ReadInput request (register=%s) to %s",
                                current_register,
                                client_addr,
                            )
                            last_sent_register = current_register
                            last_sent_time = now
                        advance_next_register()
                    if cycle_complete:
                        # Only sleep after a complete cycle; in ALL mode the 4 intermediate
                        # register reads now happen back-to-back without unnecessary delay.
                        try:
                            source = "configured"
                            if sleep_time != configured_sleep_time:
                                # Try to resolve inverter id for helpful debugging context
                                try:
                                    inverter_id = _resolve_inverter_id(resolved_dongle_serial, self.__logger)
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
                        await asyncio.sleep(sleep_time)

                except asyncio.TimeoutError:
                    self.__logger.warning(
                        "Timeout waiting for data from %s",
                        client_addr
                    )
                    # Keep polling on timeout in case the previous response was dropped.
                    if dongle_serial:
                        # Update plan (may force full poll if cache is empty)
                        registers = get_current_plan()
                        current_register = get_next_register()
                        request = build_poll_request(current_register)
                        now = time.time()
                        if last_sent_register == current_register and now - last_sent_time < last_send_guard_seconds:
                            self.__logger.info(
                                "Skipping duplicate resend for register=%s to %s (recently sent)",
                                current_register,
                                client_addr,
                            )
                        else:
                            writer.write(request)
                            await writer.drain()
                            self.__logger.info(
                                "Resent ReadInput request (register=%s) to %s after timeout",
                                current_register,
                                client_addr,
                            )
                            last_sent_register = current_register
                            last_sent_time = now
                        advance_next_register()
                    await asyncio.sleep(sleep_time)
                except Exception as e:
                    self.__logger.exception(
                        "Error handling data from %s: %s",
                        client_addr,
                        e
                    )
                    break

        finally:
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
