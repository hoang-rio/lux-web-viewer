from datetime import datetime, timedelta

from aiohttp.aiohttp import web

from . import config
from .db import dict_factory, get_db_connection


async def hourly_chart(request: web.Request):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        date_str = request.rel_url.query.get('date')
        if date_str:
            try:
                query_date = datetime.strptime(date_str, "%Y-%m-%d")
            except Exception:
                query_date = datetime.now()
        else:
            query_date = datetime.now()
        start_of_day = query_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)
        hourly_chart = cursor.execute(
            "SELECT * FROM hourly_chart WHERE datetime >= ? AND datetime < ?",
            (start_of_day.strftime("%Y-%m-%d %H:%M:%S"), end_of_day.strftime("%Y-%m-%d %H:%M:%S"))
        ).fetchall()
        return web.json_response(hourly_chart)
    except Exception as e:
        config.logger.error(f"Error in hourly_chart: {e}")
        return web.json_response([])


async def total(_: web.Request):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.row_factory = dict_factory
        total = cursor.execute(
            "SELECT SUM(pv) as pv, SUM(battery_charged) as battery_charged, SUM(battery_discharged) as battery_discharged, SUM(grid_import) as grid_import, SUM(grid_export) as grid_export, SUM(consumption) as consumption FROM daily_chart"
        ).fetchone()
        return web.json_response(total)
    except Exception as e:
        config.logger.error(f"Error in total: {e}")
        return web.json_response({})


async def daily_chart(_: web.Request):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        request = _
        month_str = request.rel_url.query.get('month')
        if month_str:
            # Expect format YYYY-MM
            try:
                year, month = map(int, month_str.split('-'))
                query_date = datetime(year, month, 1)
            except Exception:
                query_date = datetime.now()
                year = query_date.year
                month = query_date.month
        else:
            query_date = datetime.now()
            year = query_date.year
            month = query_date.month
        daily_chart = cursor.execute(
            "SELECT * FROM daily_chart WHERE year = ? AND month = ?",
            (year, month)
        ).fetchall()
        # Fill empty data to daily_chart from last item to last day of month
        if daily_chart:
            last_day_of_month = (query_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            last_item_date = datetime.strptime(daily_chart[-1][3], "%Y-%m-%d")
            while last_item_date < last_day_of_month:
                last_item_date += timedelta(days=1)
                daily_chart.append((
                    last_item_date.strftime("%Y%m%d"),
                    last_item_date.year,
                    last_item_date.month,
                    last_item_date.strftime("%Y-%m-%d"),
                    0, 0, 0, 0, 0, 0, ""
                ))
        return web.json_response(daily_chart)
    except Exception as e:
        config.logger.error(f"Error in daily_chart: {e}")
        return web.json_response([])


async def monthly_chart(request: web.Request):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        now = datetime.now()
        year_param = request.rel_url.query.get("year")
        try:
            year = int(year_param) if year_param else now.year
        except Exception:
            year = now.year

        monthly_chart = cursor.execute(
            "SELECT id, id, id, month || '/' || year as month, SUM(pv), SUM(battery_charged), SUM(battery_discharged), SUM(grid_import), SUM(grid_export), SUM(consumption) FROM daily_chart WHERE year = ? GROUP BY month",
            (year,)
        ).fetchall()

        # Provide available years for UI selection
        years_rows = cursor.execute("SELECT DISTINCT year FROM daily_chart ORDER BY year DESC").fetchall()
        years = [int(r[0]) for r in years_rows]

        return web.json_response({"chart": monthly_chart, "years": years})
    except Exception as e:
        config.logger.error(f"Error in monthly_chart: {e}")
        return web.json_response({"chart": [], "years": []})


async def yearly_chart(_: web.Request):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        yearly_chart = cursor.execute(
            "SELECT id, id, id, year || '' as year, SUM(pv), SUM(battery_charged), SUM(battery_discharged), SUM(grid_import), SUM(grid_export), SUM(consumption) FROM daily_chart GROUP BY year"
        ).fetchall()
        return web.json_response(yearly_chart)
    except Exception as e:
        config.logger.error(f"Error in yearly_chart: {e}")
        return web.json_response([])