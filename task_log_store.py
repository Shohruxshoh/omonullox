"""
task_log_store.py — TaskLog CRUD: task tarixini PostgreSQL da saqlaydi.

Har bir task uchun:
    - Qaysi API key bilan keldi
    - Qaysi servis (views / reactions / shares)
    - Necha foiz bajarildi (done / total * 100)
    - Qachon boshlandi, tugadi, nima xato bo'ldi
"""

import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from database import SessionLocal
from models import TaskLog


def _db() -> Session:
    return SessionLocal()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── CREATE ───────────────────────────────────────────────────────────────────

def create(
    task_id: str,
    api_key: str,
    service: str,
    post_link: str,
    priority: int,
    meta: dict = None,
) -> dict:
    """Yangi task yozuvini DB ga qo'shadi (status=queued)."""
    with _db() as db:
        log = TaskLog(
            task_id=task_id,
            api_key=api_key,
            service=service,
            post_link=post_link,
            priority=priority,
            status="queued",
            task_meta=json.dumps(meta) if meta else None,
            created_at=_now(),
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return _to_dict(log)


# ─── UPDATE ───────────────────────────────────────────────────────────────────

def set_running(task_id: str, total: int) -> None:
    """Task ishga tushganda total va started_at ni yozadi."""
    with _db() as db:
        log = db.get(TaskLog, task_id)
        if log:
            log.status     = "running"
            log.total      = total
            log.started_at = _now()
            db.commit()


def inc_done(task_id: str, amount: int = 1) -> None:
    """done ni oshirib boradi (har bir akkaunt tugaganda)."""
    with _db() as db:
        log = db.get(TaskLog, task_id)
        if log:
            log.done = min(log.done + amount, log.total)
            db.commit()


def set_done(task_id: str) -> None:
    """Task muvaffaqiyatli tugaganida chaqiriladi."""
    with _db() as db:
        log = db.get(TaskLog, task_id)
        if log:
            log.status      = "done"
            log.finished_at = _now()
            db.commit()


def set_error(task_id: str, error: str) -> None:
    """Task texnik xato bilan tugaganida chaqiriladi."""
    with _db() as db:
        log = db.get(TaskLog, task_id)
        if log:
            log.status      = "error"
            log.error       = error
            log.finished_at = _now()
            db.commit()


def set_rejected(task_id: str, notification: str) -> None:
    """Checker tomonidan rad etilganda chaqiriladi (reaction mumkin emas va h.k.)."""
    with _db() as db:
        log = db.get(TaskLog, task_id)
        if log:
            log.status      = "rejected"
            log.error       = notification
            log.finished_at = _now()
            db.commit()


# ─── READ ─────────────────────────────────────────────────────────────────────

def get(task_id: str) -> dict | None:
    with _db() as db:
        log = db.get(TaskLog, task_id)
        return _to_dict(log) if log else None


def get_for_api_key(task_id: str, api_key: str) -> dict | None:
    with _db() as db:
        log = (
            db.query(TaskLog)
            .filter(TaskLog.task_id == task_id, TaskLog.api_key == api_key)
            .first()
        )
        return _to_dict(log) if log else None


def list_all(limit: int = 50, offset: int = 0) -> list[dict]:
    """Barcha tasklarni yangiligidan eskisiga qarab qaytaradi (pagination bilan)."""
    with _db() as db:
        rows = (
            db.query(TaskLog)
            .order_by(TaskLog.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [_to_dict(r) for r in rows]


def list_by_api_key(api_key: str, limit: int = 50, offset: int = 0) -> list[dict]:
    with _db() as db:
        rows = (
            db.query(TaskLog)
            .filter(TaskLog.api_key == api_key)
            .order_by(TaskLog.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [_to_dict(r) for r in rows]


def stats_by_key() -> list[dict]:
    """Har bir API key uchun umumiy statistika."""
    with _db() as db:
        rows = db.query(TaskLog).all()

    agg: dict[str, dict] = {}
    for r in rows:
        k = r.api_key
        if k not in agg:
            agg[k] = {
                "api_key": k,
                "total_tasks": 0,
                "done_tasks": 0,
                "error_tasks": 0,
                "queued_tasks": 0,
                "running_tasks": 0,
            }
        agg[k]["total_tasks"] += 1
        if r.status == "done":
            agg[k]["done_tasks"] += 1
        elif r.status == "error":
            agg[k]["error_tasks"] += 1
        elif r.status == "queued":
            agg[k]["queued_tasks"] += 1
        elif r.status == "running":
            agg[k]["running_tasks"] += 1

    return list(agg.values())


# ─── INTERNAL ─────────────────────────────────────────────────────────────────

def _to_dict(log: TaskLog) -> dict:
    total = log.total or 0
    done  = log.done  or 0
    percent = round(done / total * 100, 1) if total > 0 else 0.0

    return {
        "task_id":     log.task_id,
        "api_key":     log.api_key,
        "service":     log.service,
        "post_link":   log.post_link,
        "priority":    log.priority,
        "status":      log.status,
        "total":       total,
        "done":        done,
        "percent":     percent,
        "error":       log.error,
        "task_meta":   log.task_meta,
        "created_at":  log.created_at,
        "started_at":  log.started_at,
        "finished_at": log.finished_at,
    }
