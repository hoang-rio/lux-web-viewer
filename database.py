import sqlite3
from datetime import datetime, timedelta

from time_utils import DATETIME_FORMAT, format_datetime


def insert_hourly_chart(db_connection: sqlite3.Connection, inverter_data: dict, sleep_time: int):
    """
    Insert or update hourly chart data into the database.
    
    Args:
        db_connection: SQLite database connection
        inverter_data: Dictionary containing inverter data
        sleep_time: Sleep time in seconds
        
    Returns:
        List with chart item data [id, datetime, pv, battery, grid, consumption, soc]
    """
    cursor = db_connection.cursor()
    device_time = datetime.strptime(inverter_data["deviceTime"], DATETIME_FORMAT)
    # Remove hourly_chart records older than 30 days
    oldest_date = (device_time - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
    cursor.execute(
        "DELETE FROM hourly_chart WHERE datetime < ?", (format_datetime(oldest_date),))
    item_id = device_time.strftime("%Y%m%d%H%M")
    grid = inverter_data["p_to_grid"] - inverter_data["p_to_user"]
    consumption = inverter_data["p_inv"] + \
        inverter_data["p_to_user"] - \
        inverter_data["p_rec"] + inverter_data["p_eps"]
    hourly_chart_item = {
        "id": item_id,
        "datetime": inverter_data["deviceTime"],
        "pv": inverter_data["p_pv"],
        "battery": inverter_data["p_discharge"] - inverter_data["p_charge"],
        "grid": grid,
        "consumption": consumption,
        "soc": inverter_data["soc"],
    }
    from web_viewer import dict_factory
    cursor.row_factory = dict_factory
    exist_item = cursor.execute(
        "SELECT * FROM hourly_chart WHERE id = ?", (
            item_id,)
    ).fetchone()
    if exist_item is None:
        cursor.execute(
            "INSERT INTO hourly_chart (id, datetime, pv, battery, grid, consumption, soc) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item_id, hourly_chart_item["datetime"], hourly_chart_item["pv"], hourly_chart_item["battery"],
                hourly_chart_item["grid"], hourly_chart_item["consumption"], hourly_chart_item["soc"]),
        )
    else:
        sleep_count_since_zero = max(int(device_time.second / sleep_time), 1)
        total_count = sleep_count_since_zero + 1
        pv_average = round((hourly_chart_item["pv"] * sleep_count_since_zero + exist_item["pv"]) / total_count)
        battery_average = round((hourly_chart_item["battery"] * sleep_count_since_zero + exist_item["battery"]) / total_count)
        grid_average = round((hourly_chart_item["grid"] * sleep_count_since_zero + exist_item["grid"]) / total_count)
        consumption_average = round((hourly_chart_item["consumption"] * sleep_count_since_zero + exist_item["consumption"]) / total_count)
        soc_average = round((hourly_chart_item["soc"] * sleep_count_since_zero + exist_item["soc"]) / total_count)
        cursor.execute(
            "UPDATE hourly_chart SET datetime = ?, pv = ?, battery = ?, grid = ?, consumption = ?, soc = ? WHERE id = ?",
            (
                hourly_chart_item["datetime"],
                pv_average,
                battery_average,
                grid_average,
                consumption_average,
                soc_average,
                item_id)
        )
    cursor.close()
    db_connection.commit()
    return [item_id, hourly_chart_item["datetime"], hourly_chart_item["pv"], hourly_chart_item["battery"], hourly_chart_item["grid"], hourly_chart_item["consumption"], hourly_chart_item["soc"]]


def insert_daily_chart(db_connection: sqlite3.Connection, inverter_data: dict):
    """
    Insert or update daily chart data into the database.
    
    Args:
        db_connection: SQLite database connection
        inverter_data: Dictionary containing inverter data
    """
    device_time = datetime.strptime(inverter_data["deviceTime"], DATETIME_FORMAT)
    if device_time.hour == 0 and device_time.minute == 0:
        # Igore daily data in first minute of the day
        return
    cursor = db_connection.cursor()
    item_id = device_time.strftime("%Y%m%d")
    consumption = (
        inverter_data["e_inv_day"] +
        inverter_data["e_to_user_day"] +
        inverter_data["e_eps_day"] -
        inverter_data["e_rec_day"]
    )
    daily_chart_item = {
        "id": item_id,
        "year": device_time.year,
        "month": device_time.month,
        "date": device_time.strftime("%Y-%m-%d"),
        "pv": inverter_data["e_pv_day"],
        "battery_charged": inverter_data["e_chg_day"],
        "battery_discharged": inverter_data["e_dischg_day"],
        "grid_import": inverter_data["e_to_user_day"],
        "grid_export": inverter_data["e_to_grid_day"],
        "consumption": round(consumption, 1),
        "updated": format_datetime()
    }
    is_exist = cursor.execute(
        "SELECT id, consumption FROM daily_chart WHERE id = ?", (item_id,)
    ).fetchone()
    if is_exist is None:
        cursor.execute(
            "INSERT INTO daily_chart (id, year, month, date, pv, battery_charged, battery_discharged, grid_import, grid_export, consumption, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, daily_chart_item["year"], daily_chart_item["month"], daily_chart_item["date"], daily_chart_item["pv"], daily_chart_item["battery_charged"],
                daily_chart_item["battery_discharged"], daily_chart_item["grid_import"], daily_chart_item["grid_export"],
                daily_chart_item["consumption"], daily_chart_item["updated"]),
        )
    else:
        cursor.execute(
                "UPDATE daily_chart SET year = ?, month = ?, date = ?, pv = ?, battery_charged = ?, battery_discharged = ?, grid_import = ?, grid_export = ?, consumption = ?, updated =? WHERE id = ?",
                (
                    daily_chart_item["year"],
                    daily_chart_item["month"],
                    daily_chart_item["date"],
                    daily_chart_item["pv"],
                    daily_chart_item["battery_charged"],
                    daily_chart_item["battery_discharged"],
                    daily_chart_item["grid_import"],
                    daily_chart_item["grid_export"],
                    daily_chart_item["consumption"],
                    daily_chart_item["updated"],
                    item_id)
            )
    cursor.close()
    db_connection.commit()
