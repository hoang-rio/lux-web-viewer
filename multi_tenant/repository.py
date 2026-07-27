from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .models import (
    DailyChart,
    EmailVerificationToken,
    HourlyChart,
    Inverter,
    InverterLatestState,
    NotificationHistory,
    PasswordResetToken,
    RefreshToken,
    ScopedSetting,
    User,
    UserDeviceToken,
    get_current_datetime,
)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def get_user_by_email(session: Session, email: str) -> Optional[User]:
    return session.execute(select(User).where(User.email == email)).scalar_one_or_none()


def get_user_by_id(session: Session, user_id: uuid.UUID) -> Optional[User]:
    return session.get(User, user_id)


def create_user(session: Session, email: str, password_hash: str) -> User:
    user = User(email=email, password_hash=password_hash, email_confirmed=False)
    session.add(user)
    session.flush()
    return user


def update_user_password(session: Session, user: User, password_hash: str) -> None:
    user.password_hash = password_hash
    user.updated_at = get_current_datetime()
    session.flush()


def mark_user_email_confirmed(session: Session, user: User) -> None:
    user.email_confirmed = True
    user.updated_at = get_current_datetime()
    session.flush()


# ---------------------------------------------------------------------------
# Refresh tokens
# ---------------------------------------------------------------------------

def create_refresh_token_record(
    session: Session, user_id: uuid.UUID, token_hash: str, expires_at: datetime
) -> RefreshToken:
    rt = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
    session.add(rt)
    session.flush()
    return rt


def get_active_refresh_token(session: Session, token: str) -> Optional[RefreshToken]:
    token_hash = _hash_token(token)
    now = get_current_datetime()
    return session.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
    ).scalar_one_or_none()


def revoke_refresh_token(session: Session, rt: RefreshToken) -> None:
    rt.revoked_at = get_current_datetime()
    session.flush()


def revoke_all_user_refresh_tokens(session: Session, user_id: uuid.UUID) -> None:
    session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=get_current_datetime())
    )
    session.flush()


# ---------------------------------------------------------------------------
# Email verification tokens
# ---------------------------------------------------------------------------

def create_email_verification_token(
    session: Session, user_id: uuid.UUID, token_hash: str, ttl_hours: int = 24
) -> EmailVerificationToken:
    evt = EmailVerificationToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=get_current_datetime() + timedelta(hours=ttl_hours),
    )
    session.add(evt)
    session.flush()
    return evt


def get_valid_email_verification_token(
    session: Session, token: str
) -> Optional[EmailVerificationToken]:
    token_hash = _hash_token(token)
    now = get_current_datetime()
    return session.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == token_hash,
            EmailVerificationToken.used_at.is_(None),
            EmailVerificationToken.expires_at > now,
        )
    ).scalar_one_or_none()


def use_email_verification_token(
    session: Session, evt: EmailVerificationToken
) -> None:
    evt.used_at = get_current_datetime()
    session.flush()


# ---------------------------------------------------------------------------
# Password reset tokens
# ---------------------------------------------------------------------------

def create_password_reset_token(
    session: Session, user_id: uuid.UUID, token_hash: str, ttl_hours: int = 1
) -> PasswordResetToken:
    prt = PasswordResetToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=get_current_datetime() + timedelta(hours=ttl_hours),
    )
    session.add(prt)
    session.flush()
    return prt


def get_valid_password_reset_token(
    session: Session, token: str
) -> Optional[PasswordResetToken]:
    token_hash = _hash_token(token)
    now = get_current_datetime()
    return session.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
    ).scalar_one_or_none()


def use_password_reset_token(session: Session, prt: PasswordResetToken) -> None:
    prt.used_at = get_current_datetime()
    session.flush()


# ---------------------------------------------------------------------------
# Inverters
# ---------------------------------------------------------------------------

def get_inverters_by_user(session: Session, user_id: uuid.UUID) -> list[Inverter]:
    return list(
        session.execute(
            select(Inverter).where(
                Inverter.user_id == user_id, Inverter.is_active.is_(True)
            )
        )
        .scalars()
        .all()
    )


def get_active_inverters_count(session: Session) -> int:
    return session.execute(
        select(func.count()).where(Inverter.is_active.is_(True))
    ).scalar_one()


def get_inverter_by_id_and_user(
    session: Session, inverter_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[Inverter]:
    return session.execute(
        select(Inverter).where(
            Inverter.id == inverter_id, Inverter.user_id == user_id
        )
    ).scalar_one_or_none()


def get_inverter_by_id(session: Session, inverter_id: uuid.UUID) -> Optional[Inverter]:
    return session.get(Inverter, inverter_id)


def get_inverter_by_dongle_serial(
    session: Session, dongle_serial: str
) -> Optional[Inverter]:
    return session.execute(
        select(Inverter).where(
            Inverter.dongle_serial == dongle_serial, Inverter.is_active.is_(True)
        )
    ).scalar_one_or_none()


def get_inverter_by_invert_serial(
    session: Session, invert_serial: str
) -> Optional[Inverter]:
    normalized = str(invert_serial or "").strip()
    if not normalized:
        return None
    return session.execute(
        select(Inverter).where(
            Inverter.invert_serial == normalized, Inverter.is_active.is_(True)
        )
    ).scalar_one_or_none()


def create_inverter(
    session: Session,
    user_id: uuid.UUID,
    name: str,
    dongle_serial: str,
    invert_serial: str | None,
) -> Inverter:
    inv = Inverter(
        user_id=user_id,
        name=name,
        dongle_serial=dongle_serial,
        invert_serial=(str(invert_serial).strip() if invert_serial else None),
    )
    session.add(inv)
    session.flush()
    return inv


def deactivate_inverter(session: Session, inverter: Inverter) -> None:
    inverter.is_active = False
    inverter.updated_at = get_current_datetime()
    session.flush()


def delete_inverter_hard(session: Session, inverter: Inverter) -> None:
    session.delete(inverter)
    session.flush()


def update_inverter(
    session: Session,
    inverter: Inverter,
    *,
    name: str,
) -> Inverter:
    inverter.name = name
    inverter.updated_at = get_current_datetime()
    session.flush()
    return inverter


def update_inverter_invert_serial(
    session: Session,
    inverter: Inverter,
    invert_serial: str,
) -> Inverter:
    inverter.invert_serial = str(invert_serial).strip() or None
    inverter.updated_at = get_current_datetime()
    session.flush()
    return inverter


# ---------------------------------------------------------------------------
# Latest state
# ---------------------------------------------------------------------------

def upsert_inverter_latest_state(
    session: Session,
    inverter_id: uuid.UUID,
    device_time: datetime,
    payload: dict,
) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(InverterLatestState)
        .values(
            inverter_id=inverter_id,
            device_time=device_time,
            payload=payload,
            updated_at=get_current_datetime(),
        )
        .on_conflict_do_update(
            index_elements=["inverter_id"],
            set_={
                "device_time": device_time,
                "payload": payload,
                "updated_at": get_current_datetime(),
            },
        )
    )
    session.execute(stmt)
    session.flush()


def get_inverter_latest_state(
    session: Session, inverter_id: uuid.UUID
) -> Optional[InverterLatestState]:
    return session.get(InverterLatestState, inverter_id)


# ---------------------------------------------------------------------------
# Hourly chart
# ---------------------------------------------------------------------------

def upsert_hourly_chart(
    session: Session,
    inverter_id: uuid.UUID,
    dt: datetime,
    sleep_time: int,
    pv: int,
    battery: int,
    grid: int,
    consumption: int,
    soc: int,
    sample_second: int = 0,
) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    minute_bucket = dt.replace(second=0, microsecond=0)

    existing = session.execute(
        select(HourlyChart)
        .where(
            HourlyChart.inverter_id == inverter_id,
            func.date_trunc("minute", HourlyChart.datetime) == minute_bucket,
        )
        .order_by(HourlyChart.datetime.desc())
        .limit(1)
    ).scalars().first()

    if existing is None:
        stmt = pg_insert(HourlyChart).values(
            inverter_id=inverter_id,
            datetime=dt,
            pv=pv,
            battery=battery,
            grid=grid,
            consumption=consumption,
            soc=soc,
        )
        session.execute(stmt)
    else:
        sleep_count = max(int(sample_second / sleep_time), 1)
        total = sleep_count + 1
        session.execute(
            update(HourlyChart)
            .where(
                HourlyChart.inverter_id == inverter_id,
                HourlyChart.datetime == existing.datetime,
            )
            .values(
                datetime=dt,
                pv=round((pv * sleep_count + existing.pv) / total),
                battery=round((battery * sleep_count + existing.battery) / total),
                grid=round((grid * sleep_count + existing.grid) / total),
                consumption=round(
                    (consumption * sleep_count + existing.consumption) / total
                ),
                soc=round((soc * sleep_count + existing.soc) / total),
            )
        )
    session.flush()


def get_hourly_chart(
    session: Session, inverter_id: uuid.UUID, day: date
) -> list[HourlyChart]:
    start = datetime(day.year, day.month, day.day)
    end = start + timedelta(days=1)
    return list(
        session.execute(
            select(HourlyChart)
            .where(
                HourlyChart.inverter_id == inverter_id,
                HourlyChart.datetime >= start,
                HourlyChart.datetime < end,
            )
            .order_by(HourlyChart.datetime)
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# Daily chart
# ---------------------------------------------------------------------------

def upsert_daily_chart(
    session: Session,
    inverter_id: uuid.UUID,
    d: date,
    year: int,
    month: int,
    pv: float,
    battery_charged: float,
    battery_discharged: float,
    grid_import: float,
    grid_export: float,
    consumption: float,
) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(DailyChart)
        .values(
            inverter_id=inverter_id,
            date=d,
            year=year,
            month=month,
            pv=pv,
            battery_charged=battery_charged,
            battery_discharged=battery_discharged,
            grid_import=grid_import,
            grid_export=grid_export,
            consumption=consumption,
            updated_at=get_current_datetime(),
        )
        .on_conflict_do_update(
            index_elements=["inverter_id", "date"],
            set_={
                "pv": pv,
                "battery_charged": battery_charged,
                "battery_discharged": battery_discharged,
                "grid_import": grid_import,
                "grid_export": grid_export,
                "consumption": consumption,
                "updated_at": get_current_datetime(),
            },
        )
    )
    session.execute(stmt)
    session.flush()


def get_daily_chart(
    session: Session, inverter_id: uuid.UUID, year: int, month: int
) -> list[DailyChart]:
    return list(
        session.execute(
            select(DailyChart)
            .where(
                DailyChart.inverter_id == inverter_id,
                DailyChart.year == year,
                DailyChart.month == month,
            )
            .order_by(DailyChart.date)
        )
        .scalars()
        .all()
    )


def get_monthly_chart(
    session: Session, inverter_id: uuid.UUID, year: int
) -> list[dict]:
    rows = session.execute(
        select(
            DailyChart.month,
            func.sum(DailyChart.pv).label("pv"),
            func.sum(DailyChart.battery_charged).label("battery_charged"),
            func.sum(DailyChart.battery_discharged).label("battery_discharged"),
            func.sum(DailyChart.grid_import).label("grid_import"),
            func.sum(DailyChart.grid_export).label("grid_export"),
            func.sum(DailyChart.consumption).label("consumption"),
        )
        .where(
            DailyChart.inverter_id == inverter_id,
            DailyChart.year == year,
        )
        .group_by(DailyChart.month)
        .order_by(DailyChart.month)
    ).all()
    # Convert Decimal values to float for JSON serialization
    numeric_fields = [
        "pv",
        "battery_charged",
        "battery_discharged",
        "grid_import",
        "grid_export",
        "consumption",
    ]
    result = []
    for r in rows:
        row_dict = r._asdict()
        for field in numeric_fields:
            if row_dict.get(field) is not None:
                row_dict[field] = float(row_dict[field])
        result.append(row_dict)
    return result


def get_available_years(session: Session, inverter_id: uuid.UUID) -> list[int]:
    return list(
        session.execute(
            select(DailyChart.year)
            .where(DailyChart.inverter_id == inverter_id)
            .distinct()
            .order_by(DailyChart.year.desc())
        ).scalars()
    )


def get_yearly_chart(session: Session, inverter_id: uuid.UUID) -> list[dict]:
    rows = session.execute(
        select(
            DailyChart.year,
            func.sum(DailyChart.pv).label("pv"),
            func.sum(DailyChart.battery_charged).label("battery_charged"),
            func.sum(DailyChart.battery_discharged).label("battery_discharged"),
            func.sum(DailyChart.grid_import).label("grid_import"),
            func.sum(DailyChart.grid_export).label("grid_export"),
            func.sum(DailyChart.consumption).label("consumption"),
        )
        .where(DailyChart.inverter_id == inverter_id)
        .group_by(DailyChart.year)
        .order_by(DailyChart.year)
    ).all()
    # Convert Decimal values to float for JSON serialization
    numeric_fields = [
        "pv",
        "battery_charged",
        "battery_discharged",
        "grid_import",
        "grid_export",
        "consumption",
    ]
    result = []
    for r in rows:
        row_dict = r._asdict()
        for field in numeric_fields:
            if row_dict.get(field) is not None:
                row_dict[field] = float(row_dict[field])
        result.append(row_dict)
    return result


def get_total(session: Session, inverter_id: uuid.UUID) -> dict:
    row = session.execute(
        select(
            func.sum(DailyChart.pv).label("pv"),
            func.sum(DailyChart.battery_charged).label("battery_charged"),
            func.sum(DailyChart.battery_discharged).label("battery_discharged"),
            func.sum(DailyChart.grid_import).label("grid_import"),
            func.sum(DailyChart.grid_export).label("grid_export"),
            func.sum(DailyChart.consumption).label("consumption"),
        ).where(DailyChart.inverter_id == inverter_id)
    ).one()
    result = row._asdict()
    # Convert Decimal values to float for JSON serialization
    numeric_fields = [
        "pv",
        "battery_charged",
        "battery_discharged",
        "grid_import",
        "grid_export",
        "consumption",
    ]
    for field in numeric_fields:
        if result.get(field) is not None:
            result[field] = float(result[field])
    return result


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def insert_notification(
    session: Session,
    user_id: uuid.UUID,
    title: str,
    body: str,
    inverter_id: Optional[uuid.UUID] = None,
) -> NotificationHistory:
    n = NotificationHistory(
        user_id=user_id, inverter_id=inverter_id, title=title, body=body
    )
    session.add(n)
    session.flush()
    return n


def get_notification_history(
    session: Session, user_id: uuid.UUID, limit: int = 100
) -> list[NotificationHistory]:
    return list(
        session.execute(
            select(NotificationHistory)
            .where(NotificationHistory.user_id == user_id)
            .order_by(NotificationHistory.notified_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def get_unread_notification_count(session: Session, user_id: uuid.UUID) -> int:
    return session.execute(
        select(func.count()).where(
            NotificationHistory.user_id == user_id,
            NotificationHistory.read.is_(False),
        )
    ).scalar_one()


def mark_notifications_read(session: Session, user_id: uuid.UUID) -> None:
    session.execute(
        update(NotificationHistory)
        .where(
            NotificationHistory.user_id == user_id,
            NotificationHistory.read.is_(False),
        )
        .values(read=True)
    )
    session.flush()


def delete_notification(session: Session, user_id: uuid.UUID, notification_id: int) -> bool:
    noti = session.execute(
        select(NotificationHistory).where(
            NotificationHistory.id == notification_id,
            NotificationHistory.user_id == user_id,
        )
    ).scalars().first()
    if noti is None:
        return False
    session.delete(noti)
    session.flush()
    return True


# ---------------------------------------------------------------------------
# Device tokens
# ---------------------------------------------------------------------------

def upsert_device_token(
    session: Session, user_id: uuid.UUID, token: str
) -> UserDeviceToken:
    existing = session.execute(
        select(UserDeviceToken).where(UserDeviceToken.token == token)
    ).scalar_one_or_none()
    if existing:
        if existing.user_id != user_id:
            existing.user_id = user_id
            session.flush()
        return existing
    dt = UserDeviceToken(user_id=user_id, token=token)
    session.add(dt)
    session.flush()
    return dt


def get_device_tokens_by_user(session: Session, user_id: uuid.UUID) -> list[str]:
    return list(
        session.execute(
            select(UserDeviceToken.token).where(
                UserDeviceToken.user_id == user_id
            )
        ).scalars()
    )


# ---------------------------------------------------------------------------
# Scoped settings (user-scoped)
# ---------------------------------------------------------------------------

def get_user_settings(session: Session, user_id: uuid.UUID) -> dict[str, str]:
    rows = session.execute(
        select(ScopedSetting).where(
            ScopedSetting.scope == "user",
            ScopedSetting.scope_id == user_id,
        )
    ).scalars()
    return {r.key: r.value for r in rows}


def get_user_setting(session: Session, user_id: uuid.UUID, key: str) -> Optional[str]:
    row = session.execute(
        select(ScopedSetting).where(
            ScopedSetting.scope == "user",
            ScopedSetting.scope_id == user_id,
            ScopedSetting.key == key,
        )
    ).scalar_one_or_none()
    return row.value if row else None


def upsert_user_setting(
    session: Session,
    user_id: uuid.UUID,
    key: str,
    value: str,
) -> ScopedSetting:
    row = session.execute(
        select(ScopedSetting).where(
            ScopedSetting.scope == "user",
            ScopedSetting.scope_id == user_id,
            ScopedSetting.key == key,
        )
    ).scalar_one_or_none()
    if row is None:
        row = ScopedSetting(scope="user", scope_id=user_id, key=key, value=value)
        session.add(row)
    else:
        row.value = value
        row.updated_at = get_current_datetime()
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Scoped Settings
# ---------------------------------------------------------------------------

def get_scoped_setting(session: Session, scope: str, scope_id: str | None, key: str) -> Optional[str]:
    row = session.execute(
        select(ScopedSetting).where(
            ScopedSetting.scope == scope,
            ScopedSetting.scope_id == scope_id,
            ScopedSetting.key == key,
        )
    ).scalar_one_or_none()
    return row.value if row else None


def upsert_scoped_setting(
    session: Session,
    scope: str,
    scope_id: str | None,
    key: str,
    value: str,
) -> ScopedSetting:
    row = session.execute(
        select(ScopedSetting).where(
            ScopedSetting.scope == scope,
            ScopedSetting.scope_id == scope_id,
            ScopedSetting.key == key,
        )
    ).scalar_one_or_none()
    if row is None:
        row = ScopedSetting(scope=scope, scope_id=scope_id, key=key, value=value)
        session.add(row)
    else:
        if row.value != value:
            row.value = value
            row.updated_at = get_current_datetime()
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Global Settings
# ---------------------------------------------------------------------------

def get_global_setting(session: Session, key: str) -> Optional[str]:
    return get_scoped_setting(session, "global", None, key)


def upsert_global_setting(
    session: Session,
    key: str,
    value: str,
) -> ScopedSetting:
    return upsert_scoped_setting(session, "global", None, key, value)
