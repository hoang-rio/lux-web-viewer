import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime
from typing import Optional

import tinytuya

logger = logging.getLogger(__name__)

DEVICES_JSON_FILE = "devices.json"

_scan_cache: dict = {}
_scan_cache_ts: float = 0.0
_scan_cache_lock = threading.Lock()
_SCAN_CACHE_TTL = 60.0  # seconds between automatic network scans


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


def _sync_scan(maxretry: int = 15) -> dict:
    try:
        result = tinytuya.deviceScan(verbose=False, maxretry=maxretry, poll=True)
        if not isinstance(result, dict):
            logger.warning("tinytuya.deviceScan returned %s instead of dict", type(result).__name__)
            return {}
        return result
    except Exception as e:
        logger.error("Tuya scan failed: %s", e)
        return {}


def _sync_scan_cached() -> dict:
    """Return a network scan result, refreshing at most once per TTL.

    A single scan is shared by all callers (trigger engine, batch status) so a
    stale/offline device cannot trigger one full broadcast scan per status call.
    The lock serializes scans: concurrent callers block briefly and reuse the
    same result instead of each running their own deviceScan().
    """
    global _scan_cache, _scan_cache_ts
    now = time.monotonic()
    with _scan_cache_lock:
        if _scan_cache and now - _scan_cache_ts < _SCAN_CACHE_TTL:
            return _scan_cache
        result = _sync_scan(maxretry=6)
        _scan_cache = result
        _scan_cache_ts = time.monotonic()
        return result


def _find_device_ip(device_id: str, scan_result: Optional[dict] = None) -> Optional[str]:
    """Find a Tuya device's current IP by matching gwId in a (cached) scan."""
    try:
        result = scan_result if scan_result is not None else _sync_scan_cached()
        for ip_addr, info in result.items():
            if not isinstance(info, dict):
                continue
            gw_id = info.get("gwId", info.get("id", ""))
            if gw_id == device_id:
                return ip_addr
    except Exception as e:
        logger.error("Failed to find device %s IP: %s", device_id, e)
    return None


def _update_device_ip(db_conn: sqlite3.Connection, device_id: str, new_ip: str):
    """Persist a device's newly discovered IP back to the database."""
    try:
        db_conn.execute("UPDATE tuya_devices SET ip = ? WHERE id = ?", (new_ip, device_id))
        db_conn.commit()
        logger.info("Updated device %s IP to %s", device_id, new_ip)
    except Exception as e:
        logger.error("Failed to update device %s IP to %s: %s", device_id, new_ip, e)


def _get_db_path(db_conn: sqlite3.Connection) -> Optional[str]:
    """Return the SQLite database file path for a connection (for worker threads)."""
    try:
        row = db_conn.execute("PRAGMA database_list").fetchone()
        return row[2] if row else None
    except Exception:
        return None


def _heal_device_ips_in_background(db_conn: sqlite3.Connection, failed: list[str], row_by_id: dict):
    """Rediscover IPs for failed devices in a background thread.

    Runs one (cached) network scan and persists any IP changes so the next
    status poll uses the corrected IP. Never blocks the caller with per-device
    retries; uses its own DB connection to avoid sharing one across threads.
    """
    def _heal():
        conn = None
        try:
            db_path = _get_db_path(db_conn)
            if db_path:
                conn = sqlite3.connect(db_path, timeout=10)
            scan_result = _sync_scan_cached()
            for dev_id in failed:
                row = row_by_id.get(dev_id)
                if row is None:
                    continue
                new_ip = _find_device_ip(dev_id, scan_result)
                if new_ip and new_ip != row[1]:
                    _update_device_ip(conn if conn is not None else db_conn, dev_id, new_ip)
        except Exception as e:
            logger.error("Background IP heal failed: %s", e)
        finally:
            if conn is not None:
                conn.close()

    threading.Thread(target=_heal, daemon=True).start()


async def get_device_status(device_id: str, ip: str, local_key: str, protocol_version: str = "3.3", db_conn: Optional[sqlite3.Connection] = None) -> dict:
    """Get the current DPS status of a Tuya device."""
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: _sync_get_status(device_id, ip, local_key, protocol_version, db_conn),
        )
        return result
    except Exception as e:
        logger.error("Failed to get status for device %s: %s", device_id, e)
        return {"error": str(e)}


def _sync_get_status(device_id: str, ip: str, local_key: str, protocol_version: str, db_conn: Optional[sqlite3.Connection] = None) -> dict:
    d = tinytuya.OutletDevice(device_id, ip, local_key, version=float(protocol_version))
    data = d.status()
    if data and "Err" in data:
        result = {"error": f"Error {data['Err']}: {data.get('Error', 'unknown')}"}
        if db_conn is not None:
            new_ip = _find_device_ip(device_id)
            if new_ip and new_ip != ip:
                _update_device_ip(db_conn, device_id, new_ip)
                result = _sync_get_status(device_id, new_ip, local_key, protocol_version)
        return result
    return data


async def control_device(
    device_id: str,
    ip: str,
    local_key: str,
    action: str,
    protocol_version: str = "3.3",
    params: Optional[dict] = None,
    db_conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """Send a control command to a Tuya device.

    Actions: turn_on, turn_off, toggle, set_value
    """
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: _sync_control(device_id, ip, local_key, action, protocol_version, params, db_conn),
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
    db_conn: Optional[sqlite3.Connection] = None,
) -> dict:
    def _try(ip_addr: str):
        d = tinytuya.OutletDevice(device_id, ip_addr, local_key, version=float(protocol_version))
        if action == "turn_on":
            return d.turn_on()
        elif action == "turn_off":
            return d.turn_off()
        elif action == "toggle":
            data = d.status()
            if data and "dps" in data:
                current = data["dps"].get("1", False)
                return d.set_status(not current)
            return False
        elif action == "set_value" and params:
            dp_id = params.get("dp", "1")
            value = params.get("value")
            return d.set_value(str(dp_id), value)
        return None

    ok = _try(ip)
    if ok is None:
        return {"error": f"Unknown action: {action}"}
    if not ok and db_conn is not None:
        new_ip = _find_device_ip(device_id)
        if new_ip and new_ip != ip:
            _update_device_ip(db_conn, device_id, new_ip)
            ok = _try(new_ip)

    if ok:
        return {"success": True, "action": action}
    return {"error": f"Failed to execute {action} on device {device_id}"}


async def get_device_status_from_db(device_id: str, db_conn: sqlite3.Connection) -> Optional[dict]:
    """Load device info from DB and get its live status."""
    row = db_conn.execute(
        "SELECT id, ip, local_key, protocol_version FROM tuya_devices WHERE id = ?",
        (device_id,),
    ).fetchone()
    if not row:
        return None
    return await get_device_status(row[0], row[1], row[2], row[3], db_conn)


async def control_device_from_db(device_id: str, action: str, db_conn: sqlite3.Connection, params: Optional[dict] = None) -> dict:
    """Load device info from DB and send a control command."""
    row = db_conn.execute(
        "SELECT id, ip, local_key, protocol_version FROM tuya_devices WHERE id = ?",
        (device_id,),
    ).fetchone()
    if not row:
        return {"error": f"Device {device_id} not found"}
    return await control_device(row[0], row[1], row[2], action, row[3], params, db_conn)


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


async def get_devices_status_batch(db_conn: sqlite3.Connection, device_ids: list[str]) -> dict:
    """Fetch live status for multiple devices in parallel."""
    loop = asyncio.get_event_loop()
    rows = db_conn.execute(
        "SELECT id, ip, local_key, protocol_version FROM tuya_devices WHERE id IN ({})".format(
            ",".join("?" * len(device_ids))
        ),
        device_ids,
    ).fetchall()
    row_by_id = {row[0]: row for row in rows}
    results: dict[str, dict] = {}

    def _fetch_one(row):
        return row[0], _sync_get_status(row[0], row[1], row[2], row[3], None)

    tasks = [loop.run_in_executor(None, _fetch_one, row) for row in rows]
    done = await asyncio.gather(*tasks, return_exceptions=True)
    failed: list[str] = []
    for item in done:
        if isinstance(item, Exception):
            continue
        dev_id, data = item
        if isinstance(data, dict) and "dps" in data:
            results[dev_id] = {"dps": data["dps"]}
        elif isinstance(data, dict) and "error" in data:
            results[dev_id] = {"error": data["error"]}
            failed.append(dev_id)
        else:
            results[dev_id] = {"dps": {}}

    # Self-heal stale IPs in the background: failures return immediately and a
    # single (cached) network scan updates the stored IPs so the next poll
    # succeeds. The request never waits on a scan or per-device retries.
    if failed:
        _heal_device_ips_in_background(db_conn, failed, row_by_id)
    return results


def get_device_mappings() -> dict:
    """Read devices.json from tinytuya wizard and return DPS mappings per device.

    Returns dict keyed by device ID, each value has 'name' and 'mapping'.
    Mapping keys are DPS IDs (strings), values have 'code', 'type', 'values'.
    Supports both dict-keyed and array formats of devices.json.
    """
    if not os.path.exists(DEVICES_JSON_FILE):
        return {}
    try:
        with open(DEVICES_JSON_FILE, "r") as f:
            data = json.load(f)
        raw_devices = data.get("devices", data) if isinstance(data, dict) else data
        result = {}
        if isinstance(raw_devices, dict):
            for dev_id, dev_info in raw_devices.items():
                if not isinstance(dev_info, dict):
                    continue
                mapping = dev_info.get("mapping", {})
                if mapping:
                    result[dev_id] = {
                        "name": dev_info.get("name", ""),
                        "mapping": mapping,
                    }
        elif isinstance(raw_devices, list):
            for dev_info in raw_devices:
                if not isinstance(dev_info, dict):
                    continue
                dev_id = dev_info.get("id", "")
                if not dev_id:
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
