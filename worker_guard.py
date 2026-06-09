import os

from sqlalchemy import text

from database import engine


WORKER_SINGLETON_LOCK_ID = int(os.environ.get("WORKER_SINGLETON_LOCK_ID", "7412051001"))


class WorkerAlreadyRunningError(RuntimeError):
    pass


def acquire_worker_lock():
    """Bitta database uchun faqat bitta worker process ishlashini kafolatlaydi."""
    connection = engine.connect()
    try:
        acquired = bool(connection.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": WORKER_SINGLETON_LOCK_ID},
        ).scalar())
        connection.commit()
    except Exception:
        connection.close()
        raise

    if not acquired:
        connection.close()
        raise WorkerAlreadyRunningError("Boshqa app/worker instance allaqachon ishlayapti")
    return connection


def release_worker_lock(connection) -> None:
    try:
        connection.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": WORKER_SINGLETON_LOCK_ID},
        )
        connection.commit()
    except Exception:
        connection.invalidate()
        raise
    finally:
        connection.close()
