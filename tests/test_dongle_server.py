import asyncio
import logging
import unittest

import modbus_service as m
import modbus_controller as mc
from dongle_server import DongleServer

DONGLE = "1234567890"
INV = "ABCDEFGHIJ"


def _holding_reply(register: int, values: bytes) -> bytes:
    return m.build_response_frame(
        dongle_serial=DONGLE,
        inverter_serial=INV,
        device_fn=m.FN_READ_HOLDING,
        register=register,
        payload=bytes([len(values)]) + values,
    )


def _input_reply(register: int, values: bytes) -> bytes:
    return m.build_response_frame(
        dongle_serial=DONGLE,
        inverter_serial=INV,
        device_fn=m.FN_READ_INPUT,
        register=register,
        payload=bytes([len(values)]) + values,
    )


class _FakeDongle:
    """Serves a DongleServer connection like a real dongle.

    Replies to fn=0x04 ReadInput polls and to fn=0x03 ReadHolding block reads.
    Mirrors the READ_LOW_FREQ poll cadence the server drives in SERVER mode.
    """

    def __init__(self, port: int, sleep_time: int):
        self.port = port
        self.sleep_time = sleep_time
        self.outstanding_reads = 0

    async def run(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        buf = bytearray()
        last_send = None
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(1024), timeout=15)
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= 8:
                    total = m.to_int(buf[4:6]) + m._FRAME_LENGTH_ADJUST
                    if total < 20 or total > 4096 or len(buf) < total:
                        break
                    frame = bytes(buf[:total])
                    del buf[:total]
                    fn = m.response_function(frame)
                    reg = m.request_register(frame)
                    if fn == m.FN_READ_HOLDING:
                        self.outstanding_reads += 1
                        vals = b"".join(
                            int(reg + i).to_bytes(2, "little") for i in range(40)
                        )
                        writer.write(_holding_reply(reg, vals))
                    elif fn == m.FN_READ_INPUT:
                        vals = b"".join(
                            int(100 + i).to_bytes(2, "little") for i in range(40)
                        )
                        writer.write(_input_reply(reg, vals))
                    await writer.drain()
                    last_send = asyncio.get_running_loop().time()
        finally:
            writer.close()
            await writer.wait_closed()


class TestServerModeReadInterleave(unittest.IsolatedAsyncioTestCase):
    async def _run_dongle_and_read(self, sleep_time: int):
        config = {
            "WORKING_MODE": "SERVER",
            "DONGLE_SERIAL": DONGLE,
            "INVERT_SERIAL": INV,
            "SERVER_MODE_HOST": "127.0.0.1",
            "SERVER_MODE_PORT": 0,
            "SERVER_MODE_TIMEOUT": 5,
            "SLEEP_TIME": sleep_time,
            "READ_INPUT_MODE": "ALL",
            "READ_LOW_FREQ_INTERVAL": 1,
        }
        server = DongleServer(logging.getLogger("dongle.server"), config)
        server_task = asyncio.create_task(server.start_server())
        await asyncio.sleep(0.1)
        sock = server._DongleServer__server.sockets[0]
        config["SERVER_MODE_PORT"] = sock.getsockname()[1]

        dongle = _FakeDongle(config["SERVER_MODE_PORT"], sleep_time)
        dongle_task = asyncio.create_task(dongle.run())
        await asyncio.sleep(0.2)

        controller = mc.ModbusController()
        controller.configure(config)
        controller.set_server(server, asyncio.get_running_loop())

        try:
            # Span 3 holding blocks (0/80/160) so several fc03 exchanges are
            # interleaved with the ReadInput poll replies.
            values = await controller.read_items(
                ["eps_seamless", "buzzer", "warning_voltage"]
            )
        finally:
            dongle_task.cancel()
            try:
                await dongle_task
            except (asyncio.CancelledError, Exception):
                pass
            server_task.cancel()
            try:
                await server_task
            except (asyncio.CancelledError, Exception):
                pass
            await server.stop_server()

        # Fake dongle answers each block with values base+offset, so each item's
        # raw value equals its register address; after kind/scale transforms:
        # eps_seamless (toggle bit 8) 21>>8=0, buzzer (toggle bit 7) 110>>7=0,
        # warning_voltage (scale 0.1) 162*0.1=16.2.
        self.assertEqual(values["eps_seamless"], 0)
        self.assertEqual(values["buzzer"], 0)
        self.assertEqual(values["warning_voltage"], 16.2)

    async def test_read_does_not_time_out_with_large_sleep_time(self):
        # Regression: the poll loop used to block in interruptible_sleep(SLEEP_TIME)
        # while a Modbus reply was already in flight, so the caller's 6s timeout
        # fired and read_items raised TimeoutError.
        await asyncio.wait_for(
            self._run_dongle_and_read(sleep_time=120), timeout=20
        )

    async def test_read_with_small_sleep_time(self):
        await asyncio.wait_for(
            self._run_dongle_and_read(sleep_time=1), timeout=20
        )


if __name__ == "__main__":
    unittest.main()