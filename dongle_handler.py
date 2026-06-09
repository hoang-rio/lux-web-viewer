
from socket import socket
import socket_client
from datetime import datetime
import logging
from typing_extensions import Optional

STATUS_MAP: dict = {
    0: "idle / standby",
    0x01: "fault",
    0x02: "programming",
    0x04: "pv supporting load first, surplus into grid",
    0x08: "pv charging battery",
    0x10: "discharge battery to support load, surplus into grid",
    0x11: "AC Bypass",
    0x14: "pv + battery discharging to support load, surplus into grid",
    0x20: "ac charging battery",
    0x28: "pv + ac charging battery",
    0x40: "battery powering EPS(grid off)",
    0x80: "pv not sufficient to power EPS? (grid off)",
    0xc0: "pv + battery powering EPS(grid off)",
    0x88: "pv powering EPS(grid off), surplus stored in battery",
}

TCP_FUNCTION_TRANSLATE = 194
READ_INPUT_MODE_INPUT1_ONLY = "INPUT1_ONLY"
READ_INPUT_MODE_ALL = "ALL"


def normalize_read_input_mode(mode: str | None) -> str:
    mode_value = (mode or READ_INPUT_MODE_ALL).strip().upper()
    if mode_value in {READ_INPUT_MODE_INPUT1_ONLY, "INPUT1", "READINPUT1", "READ_INPUT1"}:
        return READ_INPUT_MODE_INPUT1_ONLY
    return READ_INPUT_MODE_ALL


def get_read_input_registers(mode: str | None, should_read_low_freq: bool = True) -> list[int]:
    """Get list of register addresses to poll based on mode string."""
    mode_value = (mode or READ_INPUT_MODE_ALL).strip().upper()
    if mode_value == READ_INPUT_MODE_ALL:
        regs = [0]
        if should_read_low_freq:
            regs.extend([40, 80, 120])
        return regs
    if mode_value in {READ_INPUT_MODE_INPUT1_ONLY, "INPUT1", "READINPUT1", "READ_INPUT1"}:
        return [0]
    
    registers = []
    for part in mode_value.split(","):
        clean_part = part.strip()
        if clean_part in Dongle.INPUT_MAP:
            reg = Dongle.INPUT_MAP[clean_part][0]
            if reg == 0 or should_read_low_freq:
                registers.append(reg)
    return registers if registers else [0]


class Dongle():
    __client: socket | None = None
    __config: dict
    __logger: logging.Logger

    # Map input names to their register address, parser function, and label
    INPUT_MAP = {
        "INPUT1": (0, "read_input1", "ReadInput1"),
        "INPUT2": (40, "read_input2", "ReadInput2"),
        "INPUT3": (80, "read_input3", "ReadInput3"),
        "INPUT4": (120, "read_input4", "ReadInput4"),
    }

    def __init__(self, logger: logging.Logger, config: dict) -> None:
        self.__config = config
        self.__logger = logger
        self.__read_count = 0
        self.__cached_data = {}
        self.__connect_socket()

    def __get_request_plan(self) -> list[tuple[int, any, str]]:
        """Determine which registers to poll based on READ_INPUT_MODE config."""
        mode = (self.__config.get("READ_INPUT_MODE") or READ_INPUT_MODE_ALL).strip().upper()
        
        # Determine if we should read low-frequency inputs (INPUT2, 3, 4)
        # Default interval is 1 (read every time)
        interval = int(self.__config.get("READ_LOW_FREQ_INTERVAL") or 1)
        # Read on the first time (count=1), every 'interval' times, or if cache is empty
        should_read_low_freq = interval <= 1 or (self.__read_count % interval == 1) or not self.__cached_data

        # Handle legacy "ALL" mode
        if mode == READ_INPUT_MODE_ALL:
            plan = [(0, Dongle.read_input1, "ReadInput1")]
            if should_read_low_freq:
                plan.extend([
                    (40, Dongle.read_input2, "ReadInput2"),
                    (80, Dongle.read_input3, "ReadInput3"),
                    (120, Dongle.read_input4, "ReadInput4"),
                ])
            return plan
        
        # Handle legacy "INPUT1_ONLY" or "INPUT1"
        if mode in {READ_INPUT_MODE_INPUT1_ONLY, "INPUT1", "READINPUT1", "READ_INPUT1"}:
            return [(0, Dongle.read_input1, "ReadInput1")]

        # Parse comma-separated list (e.g., "INPUT1,INPUT3")
        plan = []
        for part in mode.split(","):
            clean_part = part.strip()
            if clean_part in Dongle.INPUT_MAP:
                reg, parser_name, label = Dongle.INPUT_MAP[clean_part]
                if reg == 0 or should_read_low_freq:
                    parser = getattr(Dongle, parser_name)
                    plan.append((reg, parser, label))
            else:
                self.__logger.warning("Unknown input mode part: %s", clean_part)
        
        return plan if plan else [(0, Dongle.read_input1, "ReadInput1")]

    def __connect_socket(self):
        try:
            self.__client = socket_client.connect(
                self.__config["DONGLE_TCP_HOST"],
                int(self.__config["DONGLE_TCP_PORT"])
            )
        except Exception as e:
            self.__logger.exception("Got exception when connect socket %s", e)

    def get_dongle_input(self) -> Optional[dict]:
        """Get requested input data from dongle based on configuration."""
        import time
        try:
            self.__logger.debug("Start get dongle input")
            
            if self.__client is None:
                self.__logger.info(
                    "Socket initial error. Re init for next time"
                )
                self.__connect_socket()
                return None
            
            self.__read_count += 1
            request_plan = self.__get_request_plan()

            # Drain any leftover data in the socket buffer before starting a new poll cycle
            self.__client.setblocking(False)
            try:
                while self.__client.recv(1024):
                    pass
            except Exception:
                pass
            self.__client.setblocking(True)

            for register, _, label in request_plan:
                msg = Dongle.build_read_input_request(
                    self.__config["DONGLE_SERIAL"],
                    self.__config["INVERT_SERIAL"],
                    register=register,
                )
                self.__client.send(bytes(msg))
                try:
                    # Wait up to 2 seconds for a response
                    self.__client.settimeout(2.0)
                    data = self.__client.recv(1024)
                    self.__logger.debug(
                        "%s response: %d bytes (first 8: %s)",
                        label,
                        len(data),
                        data[:8].hex() if data else "",
                    )
                    if data and data[0] != 0 and data[7] == TCP_FUNCTION_TRANSLATE:
                        # Use auto-detection to ensure we parse the correct register block
                        # even if packets arrive out of order or are delayed.
                        parsed_data = Dongle.read_input(list(data))
                        if parsed_data:
                            self.__cached_data.update(parsed_data)
                            self.__logger.debug("Parsed register block from %s response", label)
                except (TimeoutError, socket.timeout):
                    self.__logger.warning("%s timeout, continuing...", label)
                except Exception as e:
                    self.__logger.warning("%s error: %s", label, e)
                finally:
                    self.__client.settimeout(None)

                # Small delay between requests
                time.sleep(0.1)

            # Keep a tiny delay to avoid tight reconnect loops on unstable links.
            time.sleep(0.1)
            
            if self.__cached_data:
                # Always update the timestamp to now for the returned data
                result = dict(self.__cached_data)
                result['deviceTime'] = datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                self.__logger.info(
                    "Finish get dongle input (soc=%s%%, p_pv=%sW)",
                    result.get("soc", "?"),
                    result.get("p_pv", "?"),
                )
                if "v_bat" in result and (result["v_bat"] < 40 or result["v_bat"] > 58):
                    self.__logger.warning(
                        "v_bat out of range: %.1fV (expected 40-58V)",
                        result["v_bat"],
                    )
                return result
            else:
                self.__logger.info("No data parsed from dongle")
                return None
        except Exception as e:
            self.__logger.exception(
                "Get exception when get_dongle_input: %s", e)
            str_err = str(e)
            if "Broken pipe" in str_err or 'timed out' in str_err:
                self.__connect_socket()
            return None

    @staticmethod
    def to_int(ints: list[int]):
        return sum(b << (idx * 8) for idx, b in enumerate(ints))

    @staticmethod
    def parse_dongle_serial_from_header(input: list[int]) -> str:
        """Parse datalog serial from TCP header (10-byte ASCII at byte offset 8)."""
        if len(input) < 18:
            return ""
        return bytes(input[8:18]).decode("utf-8", errors="ignore").strip("\x00")

    @staticmethod
    def read_input1(input: list[int]):
        # Remove header + checksum
        data = input[20: len(input) - 2]
        dongle_serial = Dongle.parse_dongle_serial_from_header(input)
        status = Dongle.to_int(data[15:15 + 2])
        status_text = "Unknow status"
        if status in STATUS_MAP:
            status_text = STATUS_MAP[status]
        # Parse dongle serial (bytes 2-12)
        serial = bytes(data[2:13]).decode('utf-8', errors='ignore').strip('\x00')
        
        return {
            # 0 = static 1
            # 1 = R_INPUT
            # 2..12 = serial
            # 13/14 = length

            "dongle_serial": dongle_serial,
            "serial": serial,
            "status": status,
            "status_text": status_text,

            "v_pv_1": Dongle.to_int(data[17:17 + 2]) / 10.0,  # V
            "v_pv_2": Dongle.to_int(data[19:19 + 2]) / 10.0,  # V
            "v_pv_3": Dongle.to_int(data[21:21 + 2]) / 10.0,  # V

            "v_bat": Dongle.to_int(data[23:23 + 2]) / 10.0,  # V
            "soc": data[25],  # %
            "soh": data[26],  # %
            "internal_fault": Dongle.to_int(data[27:27 + 2]),

            "p_pv": Dongle.to_int(data[29:29 + 2]) + Dongle.to_int(data[31:31 + 2]) + Dongle.to_int(data[33:33 + 2]),
            "p_pv_1": Dongle.to_int(data[29:29 + 2]),  # W
            "p_pv_2": Dongle.to_int(data[31:31 + 2]),  # W
            "p_pv_3": Dongle.to_int(data[33:33 + 2]),  # W
            "p_charge": Dongle.to_int(data[35:35 + 2]),  # W
            "p_discharge": Dongle.to_int(data[37:37 + 2]),  # W
            "vacr": Dongle.to_int(data[39:39 + 2]),  # / 10.0 V
            "vacs": Dongle.to_int(data[41:41 + 2]),  # / 10.0 V
            "vact": Dongle.to_int(data[43:43 + 2]),  # / 10.0 V
            "fac": Dongle.to_int(data[45:45 + 2]),  # / 100.0 Hz

            "p_inv": Dongle.to_int(data[47:47 + 2]),  # W
            "p_rec": Dongle.to_int(data[49:49 + 2]),  # W

            # IinvRMS https://github.com/celsworth/lxp-bridge/blob/d4d2b14ed12330e62bde6bacc81bbfc4037295ee/src/lxp/packet.rs#L268-L269
            "i_inv_rms": Dongle.to_int(data[51:51 + 2]),  # W ?

            "pf": Dongle.to_int(data[53:53 + 2]) / 1000.0,  # Hz

            "v_eps_r": Dongle.to_int(data[55:55 + 2]) / 10.0,  # V
            "v_eps_s": Dongle.to_int(data[57:57 + 2]) / 10.0,  # V
            "v_eps_t": Dongle.to_int(data[59:59 + 2]) / 10.0,  # V
            "f_eps": Dongle.to_int(data[61:61 + 2]) / 100.0,  # Hz

            "p_eps": Dongle.to_int(data[63:63 + 2]),  # W
            "s_eps": Dongle.to_int(data[65:65 + 2]),  # W

            "p_to_grid": Dongle.to_int(data[67:67 + 2]),  # W
            "p_to_user": Dongle.to_int(data[69:69 + 2]),  # W

            "e_pv_day": (Dongle.to_int(data[71:71 + 2]) +
                         # kWh
                         Dongle.to_int(data[73:73 + 2]) + Dongle.to_int(data[75:75 + 2])) / 10.0,
            "e_pv_1_day": Dongle.to_int(data[71:71 + 2]) / 10.0,  # kWh
            "e_pv_2_day": Dongle.to_int(data[73:73 + 2]) / 10.0,  # kWh
            "e_pv_3_day": Dongle.to_int(data[75:75 + 2]) / 10.0,  # kWh
            "e_inv_day": Dongle.to_int(data[77:77 + 2]) / 10.0,  # kWh
            "e_rec_day": Dongle.to_int(data[79:79 + 2]) / 10.0,  # kWh
            "e_chg_day": Dongle.to_int(data[81:81 + 2]) / 10.0,  # kWh
            "e_dischg_day": Dongle.to_int(data[83:83 + 2]) / 10.0,  # kWh
            "e_eps_day": Dongle.to_int(data[85:85 + 2]) / 10.0,  # kWh
            "e_to_grid_day": Dongle.to_int(data[87:87 + 2]) / 10.0,  # kWh
            "e_to_user_day": Dongle.to_int(data[89:89 + 2]) / 10.0,  # kWh

            "v_bus_1": Dongle.to_int(data[91:91 + 2]) / 10.0,  # V
            "v_bus_2": Dongle.to_int(data[93:93 + 2]) / 10.0  # V
        }

    @staticmethod
    def to_int32(ints: list[int]):
        """Convert 4 bytes to 32-bit integer (little-endian)."""
        return sum(b << (idx * 8) for idx, b in enumerate(ints))

    @staticmethod
    def read_input2(input: list[int]):
        """Parse ReadInput2 - Energy totals, fault/warning codes, temperatures, runtime.
        
        Register 40, 80 bytes.
        """
        # Remove header + checksum
        data = input[20: len(input) - 2]
        
        return {
            # Energy totals (all time) - 4 bytes each
            "e_pv_all_1": Dongle.to_int32(data[15:19]) / 10.0,  # kWh
            "e_pv_all_2": Dongle.to_int32(data[19:23]) / 10.0,  # kWh
            "e_pv_all_3": Dongle.to_int32(data[23:27]) / 10.0,  # kWh
            "e_pv_all": round((Dongle.to_int32(data[15:19]) + Dongle.to_int32(data[19:23]) + Dongle.to_int32(data[23:27])) / 10.0, 1),
            
            "e_inv_all": Dongle.to_int32(data[27:31]) / 10.0,  # kWh
            "e_rec_all": Dongle.to_int32(data[31:35]) / 10.0,  # kWh
            "e_chg_all": Dongle.to_int32(data[35:39]) / 10.0,  # kWh
            "e_dischg_all": Dongle.to_int32(data[39:43]) / 10.0,  # kWh
            "e_eps_all": Dongle.to_int32(data[43:47]) / 10.0,  # kWh
            "e_to_grid_all": Dongle.to_int32(data[47:51]) / 10.0,  # kWh
            "e_to_user_all": Dongle.to_int32(data[51:55]) / 10.0,  # kWh
            
            # Fault and warning codes
            "fault_code": Dongle.to_int32(data[55:59]),
            "warning_code": Dongle.to_int32(data[59:63]),
            
            # Temperatures
            "t_inner": Dongle.to_int(data[63:65]),  # °C
            "t_rad_1": Dongle.to_int(data[65:67]),  # °C
            "t_rad_2": Dongle.to_int(data[67:69]),  # °C
            "t_bat": Dongle.to_int(data[69:71]),  # °C
            
            # Runtime (seconds)
            "runtime": Dongle.to_int32(data[73:77]),
        }

    @staticmethod
    def read_input3(input: list[int]):
        """Parse ReadInput3 - Battery BMS data.
        
        Register 80, 80 bytes.
        """
        # Remove header + checksum
        data = input[20: len(input) - 2]
        
        return {
            # Battery charge/discharge limits
            "max_chg_curr": Dongle.to_int(data[17:19]) / 10.0,  # A
            "max_dischg_curr": Dongle.to_int(data[19:21]) / 10.0,  # A
            "charge_volt_ref": Dongle.to_int(data[21:23]) / 10.0,  # V
            "dischg_cut_volt": Dongle.to_int(data[23:25]) / 10.0,  # V
            
            # Battery status flags
            "bat_status_0": Dongle.to_int(data[25:27]),
            "bat_status_1": Dongle.to_int(data[27:29]),
            "bat_status_2": Dongle.to_int(data[29:31]),
            "bat_status_3": Dongle.to_int(data[31:33]),
            "bat_status_4": Dongle.to_int(data[33:35]),
            "bat_status_5": Dongle.to_int(data[35:37]),
            "bat_status_6": Dongle.to_int(data[37:39]),
            "bat_status_7": Dongle.to_int(data[39:41]),
            "bat_status_8": Dongle.to_int(data[41:43]),
            "bat_status_9": Dongle.to_int(data[43:45]),
            "bat_status_inv": Dongle.to_int(data[45:47]),
            
            # Battery info
            "bat_count": Dongle.to_int(data[47:49]),
            "bat_capacity": Dongle.to_int(data[49:51]),  # Ah
            "bat_current": Dongle.to_int(data[51:53]) / 100.0,  # A
            
            # BMS events
            "bms_event_1": Dongle.to_int(data[53:55]),
            "bms_event_2": Dongle.to_int(data[55:57]),
            
            # Cell voltages and temperatures
            "max_cell_voltage": Dongle.to_int(data[57:59]) / 1000.0,  # V
            "min_cell_voltage": Dongle.to_int(data[59:61]) / 1000.0,  # V
            "max_cell_temp": Dongle.to_int(data[61:63]) / 10.0,  # °C
            "min_cell_temp": Dongle.to_int(data[63:65]) / 10.0,  # °C
            
            # BMS firmware and cycle
            "bms_fw_update_state": Dongle.to_int(data[65:67]),
            "cycle_count": Dongle.to_int(data[67:69]),
            
            # Battery voltage
            "vbat_inv": Dongle.to_int(data[69:71]) / 10.0,  # V
        }

    @staticmethod
    def read_input4(input: list[int]):
        """Parse ReadInput4 - Generator and EPS data.
        
        Register 120, 80 bytes.
        """
        # Remove header + checksum
        data = input[20: len(input) - 2]
        
        return {
            # Generator data
            "v_gen": Dongle.to_int(data[17:19]) / 10.0,  # V
            "f_gen": Dongle.to_int(data[19:21]) / 100.0,  # Hz
            "p_gen": Dongle.to_int(data[21:23]),  # W
            "e_gen_day": Dongle.to_int(data[23:25]) / 10.0,  # kWh
            "e_gen_all": Dongle.to_int32(data[25:29]) / 10.0,  # kWh
            
            # EPS L1/L2 data
            "v_eps_l1": Dongle.to_int(data[29:31]) / 10.0,  # V
            "v_eps_l2": Dongle.to_int(data[31:33]) / 10.0,  # V
            "p_eps_l1": Dongle.to_int(data[33:35]),  # W
            "p_eps_l2": Dongle.to_int(data[35:37]),  # W
            "s_eps_l1": Dongle.to_int(data[37:39]),  # VA
            "s_eps_l2": Dongle.to_int(data[39:41]),  # VA
            "e_eps_l1_day": Dongle.to_int(data[41:43]) / 10.0,  # kWh
            "e_eps_l2_day": Dongle.to_int(data[43:45]) / 10.0,  # kWh
            "e_eps_l1_all": Dongle.to_int32(data[45:49]) / 10.0,  # kWh
            "e_eps_l2_all": Dongle.to_int32(data[49:53]) / 10.0,  # kWh
        }

    @staticmethod
    def read_input(input: list[int]) -> Optional[dict]:
        """Auto-detect and parse any ReadInput type based on register offset and data length.
        
        Based on lxp-bridge protocol:
        - Register 0, 80 bytes -> ReadInput1 (real-time data)
        - Register 40, 80 bytes -> ReadInput2 (energy totals, temps, runtime)
        - Register 80, 80 bytes -> ReadInput3 (battery BMS data)
        - Register 120, 80 bytes -> ReadInput4 (generator, EPS data)
        - Register 0, 254 bytes -> ReadInputAll (combined data)
        """
        if len(input) < 38:
            return None
        
        # Strip TCP header and checksum.
        data = input[20: len(input) - 2]
        
        # Expected layout after stripping header/checksum:
        # [address(1), function(1), inverter_serial(10), register(2), value_len?(1), values...]
        if len(data) < 14:
            return None
            
        register = Dongle.to_int(data[12:14])

        # Most inverter replies include a value-length byte for ReadInput.
        # Use it when valid, otherwise fallback to inferred payload length.
        value_len: Optional[int] = None
        if len(data) >= 15:
            advertised_len = data[14]
            remaining = len(data) - 15
            if advertised_len == remaining:
                value_len = advertised_len

        if value_len is None:
            # Protocol variants without value-length byte.
            value_len = len(data) - 14
        
        try:
            # ReadInputAll - combined data (register 0, 254 bytes)
            if register == 0 and value_len == 254:
                return Dongle.read_input_all(input)
            # ReadInput1 - real-time data (register 0, 80 bytes)
            elif register == 0 and value_len == 80:
                return Dongle.read_input1(input)
            # ReadInput2 - energy totals (register 40, 80 bytes)
            elif register == 40 and value_len == 80:
                return Dongle.read_input2(input)
            # ReadInput3 - battery BMS (register 80, 80 bytes)
            elif register == 80 and value_len == 80:
                return Dongle.read_input3(input)
            # ReadInput4 - generator/EPS (register 120, 80 bytes)
            elif register == 120 and value_len == 80:
                return Dongle.read_input4(input)
            else:
                return None
        except Exception:
            return None

    @staticmethod
    def build_read_input_request(dongle_serial: str, inverter_serial: str, register: int = 0, protocol: int = 1) -> bytes:
        """Build a ReadInput request message matching the original protocol.
        
        Args:
            dongle_serial: Dongle serial number
            inverter_serial: Inverter serial number
            register: Register offset (0=ReadInput1, 40=ReadInput2, 80=ReadInput3, 120=ReadInput4)
            protocol: Protocol version (1 or 2)
        
        Returns:
            bytes: Complete TCP frame for ReadInput request
        """
        return Dongle.build_read_input_request_with_count(
            dongle_serial=dongle_serial,
            inverter_serial=inverter_serial,
            register=register,
            count=40,
            protocol=protocol,
        )

    @staticmethod
    def build_read_input_request_with_count(
        dongle_serial: str,
        inverter_serial: str,
        register: int = 0,
        count: int = 40,
        protocol: int = 1,
    ) -> bytes:
        """Build a ReadInput request frame compatible with lxp-bridge.

        Packet details:
        - Values for ReadInput are register-count (2 bytes LE), default 40
        - Inner packet checksum is CRC16 MODBUS over data[2:]
        - Header length field is frame_length - 6 (little-endian)
        """
        def serial_bytes(serial: str) -> bytes:
            raw = str(serial).encode("ascii", errors="ignore")
            return raw[:10].ljust(10, b"\x00")

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

        datalog = serial_bytes(dongle_serial)
        inverter = serial_bytes(inverter_serial)

        # Build translated-data body.
        data = bytearray([0, 0, 0, 4])
        data.extend(inverter)
        data.extend(int(register).to_bytes(2, "little", signed=False))
        data.extend(int(count).to_bytes(2, "little", signed=False))
        data[0:2] = len(data).to_bytes(2, "little", signed=False)

        checksum = crc16_modbus(data[2:])
        data.extend(checksum.to_bytes(2, "little", signed=False))

        frame_length = 18 + len(data)
        frame = bytearray([161, 26])
        frame.extend(int(protocol).to_bytes(2, "little", signed=False))
        frame.extend(int(frame_length - 6).to_bytes(2, "little", signed=False))
        frame.extend([1, TCP_FUNCTION_TRANSLATE])
        frame.extend(datalog)
        frame.extend(data)

        return bytes(frame)

    @staticmethod
    def read_input_all(input: list[int]) -> Optional[dict]:
        """Parse ReadInputAll - Combined data from all input registers.
        
        Register 0, 254 bytes.
        """
        try:
            # Remove header + checksum
            data = input[20: len(input) - 2]
            dongle_serial = Dongle.parse_dongle_serial_from_header(input)
            
            # Parse dongle serial (bytes 2-12)
            serial = bytes(data[2:13]).decode('utf-8', errors='ignore').strip('\x00')
            
            # Parse ReadInput1 section (offset 0)
            status = Dongle.to_int(data[15:17])
            status_text = STATUS_MAP.get(status, "Unknown status")
            
            # Calculate p_pv and e_pv_day
            p_pv_1 = Dongle.to_int(data[29:31])
            p_pv_2 = Dongle.to_int(data[31:33])
            p_pv_3 = Dongle.to_int(data[33:35])
            p_pv = p_pv_1 + p_pv_2 + p_pv_3
            
            e_pv_1_day = Dongle.to_int(data[71:73]) / 10.0
            e_pv_2_day = Dongle.to_int(data[73:75]) / 10.0
            e_pv_3_day = Dongle.to_int(data[75:77]) / 10.0
            e_pv_day = round(e_pv_1_day + e_pv_2_day + e_pv_3_day, 1)
            
            # Parse ReadInput2 section (offset 40)
            e_pv_all_1 = Dongle.to_int32(data[55:59]) / 10.0
            e_pv_all_2 = Dongle.to_int32(data[59:63]) / 10.0
            e_pv_all_3 = Dongle.to_int32(data[63:67]) / 10.0
            e_pv_all = round(e_pv_all_1 + e_pv_all_2 + e_pv_all_3, 1)
            
            # Parse ReadInput3 section (offset 80)
            # Parse ReadInput4 section (offset 120)
            
            return {
                "dongle_serial": dongle_serial,
                "serial": serial,
                "input_type": "all",
                
                # ReadInput1 data
                "status": status,
                "status_text": status_text,
                "v_pv_1": Dongle.to_int(data[17:19]) / 10.0,
                "v_pv_2": Dongle.to_int(data[19:21]) / 10.0,
                "v_pv_3": Dongle.to_int(data[21:23]) / 10.0,
                "v_bat": Dongle.to_int(data[23:25]) / 10.0,
                "soc": data[25],
                "soh": data[26],
                "internal_fault": Dongle.to_int(data[27:29]),
                "p_pv": p_pv,
                "p_pv_1": p_pv_1,
                "p_pv_2": p_pv_2,
                "p_pv_3": p_pv_3,
                "p_charge": Dongle.to_int(data[35:37]),
                "p_discharge": Dongle.to_int(data[37:39]),
                "vacr": Dongle.to_int(data[39:41]),
                "vacs": Dongle.to_int(data[41:43]),
                "vact": Dongle.to_int(data[43:45]),
                "fac": Dongle.to_int(data[45:47]),
                "p_inv": Dongle.to_int(data[47:49]),
                "p_rec": Dongle.to_int(data[49:51]),
                "i_inv_rms": Dongle.to_int(data[51:53]),
                "pf": Dongle.to_int(data[53:55]) / 1000.0,
                "v_eps_r": Dongle.to_int(data[55:57]) / 10.0,
                "v_eps_s": Dongle.to_int(data[57:59]) / 10.0,
                "v_eps_t": Dongle.to_int(data[59:61]) / 10.0,
                "f_eps": Dongle.to_int(data[61:63]) / 100.0,
                "p_eps": Dongle.to_int(data[63:65]),
                "s_eps": Dongle.to_int(data[65:67]),
                "p_to_grid": Dongle.to_int(data[67:69]),
                "p_to_user": Dongle.to_int(data[69:71]),
                "e_pv_day": e_pv_day,
                "e_pv_1_day": e_pv_1_day,
                "e_pv_2_day": e_pv_2_day,
                "e_pv_3_day": e_pv_3_day,
                "e_inv_day": Dongle.to_int(data[77:79]) / 10.0,
                "e_rec_day": Dongle.to_int(data[79:81]) / 10.0,
                "e_chg_day": Dongle.to_int(data[81:83]) / 10.0,
                "e_dischg_day": Dongle.to_int(data[83:85]) / 10.0,
                "e_eps_day": Dongle.to_int(data[85:87]) / 10.0,
                "e_to_grid_day": Dongle.to_int(data[87:89]) / 10.0,
                "e_to_user_day": Dongle.to_int(data[89:91]) / 10.0,
                "v_bus_1": Dongle.to_int(data[91:93]) / 10.0,
                "v_bus_2": Dongle.to_int(data[93:95]) / 10.0,
                
                # ReadInput2 data
                "e_pv_all": e_pv_all,
                "e_pv_all_1": e_pv_all_1,
                "e_pv_all_2": e_pv_all_2,
                "e_pv_all_3": e_pv_all_3,
                "e_inv_all": Dongle.to_int32(data[95:99]) / 10.0,
                "e_rec_all": Dongle.to_int32(data[99:103]) / 10.0,
                "e_chg_all": Dongle.to_int32(data[103:107]) / 10.0,
                "e_dischg_all": Dongle.to_int32(data[107:111]) / 10.0,
                "e_eps_all": Dongle.to_int32(data[111:115]) / 10.0,
                "e_to_grid_all": Dongle.to_int32(data[115:119]) / 10.0,
                "e_to_user_all": Dongle.to_int32(data[119:123]) / 10.0,
                "fault_code": Dongle.to_int32(data[123:127]),
                "warning_code": Dongle.to_int32(data[127:131]),
                "t_inner": Dongle.to_int(data[131:133]),
                "t_rad_1": Dongle.to_int(data[133:135]),
                "t_rad_2": Dongle.to_int(data[135:137]),
                "t_bat": Dongle.to_int(data[137:139]),
                "runtime": Dongle.to_int32(data[141:145]),
                
                # ReadInput3 data
                "max_chg_curr": Dongle.to_int(data[147:149]) / 10.0,
                "max_dischg_curr": Dongle.to_int(data[149:151]) / 10.0,
                "charge_volt_ref": Dongle.to_int(data[151:153]) / 10.0,
                "dischg_cut_volt": Dongle.to_int(data[153:155]) / 10.0,
                "bat_status_0": Dongle.to_int(data[155:157]),
                "bat_status_1": Dongle.to_int(data[157:159]),
                "bat_status_2": Dongle.to_int(data[159:161]),
                "bat_status_3": Dongle.to_int(data[161:163]),
                "bat_status_4": Dongle.to_int(data[163:165]),
                "bat_status_5": Dongle.to_int(data[165:167]),
                "bat_status_6": Dongle.to_int(data[167:169]),
                "bat_status_7": Dongle.to_int(data[169:171]),
                "bat_status_8": Dongle.to_int(data[171:173]),
                "bat_status_9": Dongle.to_int(data[173:175]),
                "bat_status_inv": Dongle.to_int(data[175:177]),
                "bat_count": Dongle.to_int(data[177:179]),
                "bat_capacity": Dongle.to_int(data[179:181]),
                "bat_current": Dongle.to_int(data[181:183]) / 100.0,
                "bms_event_1": Dongle.to_int(data[183:185]),
                "bms_event_2": Dongle.to_int(data[185:187]),
                "max_cell_voltage": Dongle.to_int(data[187:189]) / 1000.0,
                "min_cell_voltage": Dongle.to_int(data[189:191]) / 1000.0,
                "max_cell_temp": Dongle.to_int(data[191:193]) / 10.0,
                "min_cell_temp": Dongle.to_int(data[193:195]) / 10.0,
                "bms_fw_update_state": Dongle.to_int(data[195:197]),
                "cycle_count": Dongle.to_int(data[197:199]),
                "vbat_inv": Dongle.to_int(data[199:201]) / 10.0,
                
                # ReadInput4 data
                "v_gen": Dongle.to_int(data[217:219]) / 10.0,
                "f_gen": Dongle.to_int(data[219:221]) / 100.0,
                "p_gen": Dongle.to_int(data[221:223]),
                "e_gen_day": Dongle.to_int(data[223:225]) / 10.0,
                "e_gen_all": Dongle.to_int32(data[225:229]) / 10.0,
                "v_eps_l1": Dongle.to_int(data[229:231]) / 10.0,
                "v_eps_l2": Dongle.to_int(data[231:233]) / 10.0,
                "p_eps_l1": Dongle.to_int(data[233:235]),
                "p_eps_l2": Dongle.to_int(data[235:237]),
                "s_eps_l1": Dongle.to_int(data[237:239]),
                "s_eps_l2": Dongle.to_int(data[239:241]),
                "e_eps_l1_day": Dongle.to_int(data[241:243]) / 10.0,
                "e_eps_l2_day": Dongle.to_int(data[243:245]) / 10.0,
                "e_eps_l1_all": Dongle.to_int32(data[245:249]) / 10.0,
                "e_eps_l2_all": Dongle.to_int32(data[249:253]) / 10.0,
            }
        except Exception:
            return None
