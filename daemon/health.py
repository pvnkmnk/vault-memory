# daemon/health.py
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Response, status

router = APIRouter()

_status: str = "starting"
_degraded_reason: Optional[str] = None
_startup_time = datetime.now(timezone.utc)
_last_index_time: Optional[datetime] = None


def mark_ready():
    global _status, _degraded_reason
    _status = "ready"
    _degraded_reason = None


def mark_indexing():
    global _status
    _status = "indexing"


def mark_degraded(reason: str):
    global _status, _degraded_reason
    _status = "degraded"
    _degraded_reason = reason


def record_index_complete():
    global _last_index_time
    _last_index_time = datetime.now(timezone.utc)

async def validate_write_path(vault_path: str) -> bool:
    """
    Verify the vault write path is safe and writable.
    Returns True if safe, False if not (triggers degraded mode).
    """
    import os
    try:
        if not os.path.isdir(vault_path):
            return False
        test_file = os.path.join(vault_path, ".vault-memory-test")
        with open(test_file, "w") as f:
            f.write("")
        os.remove(test_file)
        return True
    except Exception:
        return False


@router.get("/health")
async def liveness():
    return {
        "status": "alive",
        "uptime_seconds": (datetime.now(timezone.utc) - _startup_time).total_seconds(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
async def readiness(response: Response):
    payload = {
        "status": _status,
        "uptime_seconds": (datetime.now(timezone.utc) - _startup_time).total_seconds(),
        "last_index": _last_index_time.isoformat() if _last_index_time else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if _status == "ready":
        return payload
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    if _degraded_reason:
        payload["reason"] = _degraded_reason
    return payload
