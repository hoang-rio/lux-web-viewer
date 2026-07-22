import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Optional

import tinytuya

logger = logging.getLogger(__name__)

DEVICES_JSON_FILE = "devices.json"


def is_wizard_run() -> bool:
    """Check if tinytuya wizard has been run (devices.json exists)."""
    return os.path.exists(DEVICES_JSON_FILE)


async def scan_devices() -> list[dict]:
    """Scan the local network for Tuya devices.

    Returns a list of dicts with keys: gwId, ip, version, product_id, name, local_key.
    If devices.json exists from tinytuya wizard, name and local_key are auto-filled.
    """
    loop = asyncio.get_event_loop()
    devices = await loop.run_in_executor(None, _sync_scan)
    result = []
    if not isinstance(devices, dict):
        logger.warning("deviceScan returned unexpected type: %s", type(devices).__name__)
        return result
    for ip_addr, info in devices.items():
        if not isinstance(info, dict):
            continue
        result.append({
            "ip": ip_addr,
            "gwId": info.get("gwId", info.get("id", "")),
            "version": info.get("version", "3.3"),
            "product_id": info.get("product_id", ""),
            "name": info.get("name", ""),
            "local_key": info.get("key", info.get("local_key", "")),
        })
    return result


def _sync_scan() -> dict:
    try:
        result = tinytuya.deviceScan(verbose=False, maxretry=15, poll=True)
        if not isinstance(result, dict):
            logger.warning("tinytuya.deviceScan returned %s instead of dict", type(result).__name__)
            return {}
        return result
    except Exception as e:
        logger.error("Tuya scan failed: %s", e)
        return {}


async def get_device_status(device_id: str, ip: str, local_key: str, protocol_version: str = "3.3") -> dict:
    """Get the current DPS status of a Tuya device."""
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: _sync_get_status(device_id, ip, local_key, protocol_version),
        )
        return result
    except Exception as e:
        logger.error("Failed to get status for device %s: %s", device_id, e)
        return {"error": str(e)}


def _sync_get_status(device_id: str, ip: str, local_key: str, protocol_version: str) -> dict:
    d = tinytuya.OutletDevice(device_id, ip, local_key, version=float(protocol_version))
    data = d.status()
    if data and "Err" in data:
        return {"error": f"Error {data['Err']}: {data.get('Error', 'unknown')}"}
    return data


async def control_device(
    device_id: str,
    ip: str,
    local_key: str,
    action: str,
    protocol_version: str = "3.3",
    params: Optional[dict] = None,
) -> dict:
    """Send a control command to a Tuya device.

    Actions: turn_on, turn_off, toggle, set_value
    """
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: _sync_control(device_id, ip, local_key, action, protocol_version, params),
        )
        return result
    except Exception as e:
        logger.error("Failed to control device %s: %s", device_id, e)
        return {"error": str(e)}


def _sync_control(
    device_id: str,
    ip: str,
    local_key: str,
    action: str,
    protocol_version: str,
    params: Optional[dict],
) -> dict:
    d = tinytuya.OutletDevice(device_id, ip, local_key, version=float(protocol_version))

    if action == "turn_on":
        d.turn_on()
    elif action == "turn_off":
        d.turn_off()
    elif action == "toggle":
        data = d.status()
        if data and "dps" in data:
            current = data["dps"].get("1", False)
            d.set_status(not current)
    elif action == "set_value" and params:
        dp_id = params.get("dp", "1")
        value = params.get("value")
        d.set_value(str(dp_id), value)
    else:
        return {"error": f"Unknown action: {action}"}

    return {"success": True, "action": action}


async def get_device_status_from_db(device_id: str, db_conn: sqlite3.Connection) -> Optional[dict]:
    """Load device info from DB and get its live status."""
    row = db_conn.execute(
        "SELECT id, ip, local_key, protocol_version FROM tuya_devices WHERE id = ?",
        (device_id,),
    ).fetchone()
    if not row:
        return None
    return await get_device_status(row[0], row[1], row[2], row[3])


async def control_device_from_db(device_id: str, action: str, db_conn: sqlite3.Connection, params: Optional[dict] = None) -> dict:
    """Load device info from DB and send a control command."""
    row = db_conn.execute(
        "SELECT id, ip, local_key, protocol_version FROM tuya_devices WHERE id = ?",
        (device_id,),
    ).fetchone()
    if not row:
        return {"error": f"Device {device_id} not found"}
    return await control_device(row[0], row[1], row[2], action, row[3], params)


def add_device(device_cfg: dict, db_conn: sqlite3.Connection) -> dict:
    """Insert a new Tuya device into the database."""
    db_conn.execute(
        "INSERT OR REPLACE INTO tuya_devices (id, name, ip, local_key, protocol_version, device_type, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            device_cfg["id"],
            device_cfg["name"],
            device_cfg["ip"],
            device_cfg["local_key"],
            device_cfg.get("protocol_version", "3.3"),
            device_cfg.get("device_type", "outlet"),
            datetime.now().isoformat(),
        ),
    )
    db_conn.commit()
    return device_cfg


def get_all_devices(db_conn: sqlite3.Connection) -> list[dict]:
    """Return all registered Tuya devices."""
    rows = db_conn.execute(
        "SELECT id, name, ip, local_key, protocol_version, device_type, created_at FROM tuya_devices"
    ).fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "ip": r[2],
            "local_key": r[3],
            "protocol_version": r[4],
            "device_type": r[5],
            "created_at": r[6],
        }
        for r in rows
    ]


def delete_device(device_id: str, db_conn: sqlite3.Connection) -> bool:
    """Delete a Tuya device from the database."""
    cursor = db_conn.execute("DELETE FROM tuya_devices WHERE id = ?", (device_id,))
    db_conn.commit()
    return cursor.rowcount > 0


def get_device_mappings() -> dict:
    """Read devices.json from tinytuya wizard and return DPS mappings per device.

    Returns dict keyed by device ID, each value has 'name' and 'mapping'.
    Mapping keys are DPS IDs (strings), values have 'code', 'type', 'values'.
    """
    if not os.path.exists(DEVICES_JSON_FILE):
        return {}
    try:
        with open(DEVICES_JSON_FILE, "r") as f:
            data = json.load(f)
        devices = data.get("devices", data) if isinstance(data, dict) else {}
        result = {}
        for dev_id, dev_info in devices.items():
            if not isinstance(dev_info, dict):
                continue
            mapping = dev_info.get("mapping", {})
            if mapping:
                result[dev_id] = {
                    "name": dev_info.get("name", ""),
                    "mapping": mapping,
                }
        return result
    except Exception as e:
        logger.error("Failed to read devices.json mappings: %s", e)
        return {}
