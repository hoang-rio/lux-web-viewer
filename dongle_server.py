import asyncio
import logging
from datetime import datetime
from typing import Optional
import dongle_handler


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
                writer.write(request)
                await writer.drain()
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
            
            while True:
                try:
                    # Wait for data from dongle
                    data = await asyncio.wait_for(
                        reader.read(1024),
                        timeout=int(self.__config.get("SERVER_MODE_TIMEOUT", 300))
                    )

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

                    # Send next ReadInput request after processing
                    if dongle_serial and inverter_serial:
                        current_register = get_next_register()
                        request = build_poll_request(current_register)
                        writer.write(request)
                        await writer.drain()
                        self.__logger.debug(
                            "Sent ReadInput request (register=%s) to %s",
                            current_register,
                            client_addr,
                        )
                        advance_next_register()
                    
                    # Wait for next polling interval
                    await asyncio.sleep(sleep_time)

                except asyncio.TimeoutError:
                    self.__logger.warning(
                        "Timeout waiting for data from %s",
                        client_addr
                    )
                    # Keep polling on timeout in case the previous response was dropped.
                    if dongle_serial and inverter_serial:
                        current_register = get_next_register()
                        request = build_poll_request(current_register)
                        writer.write(request)
                        await writer.drain()
                        self.__logger.debug(
                            "Resent ReadInput request (register=%s) to %s after timeout",
                            current_register,
                            client_addr,
                        )
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