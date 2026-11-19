"""
System Health Router — /api/v1/health/system

Returns:
  - process memory (RSS + percent)
  - server uptime
  - total signals generated (DB count + paper trader live count)
  - DB connection pool status
  - resolver and paper trader status
"""

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.core.database import engine, get_db
from app.models.schema import Signal

router = APIRouter(prefix="/health", tags=["Health & Diagnostics"])

# Track server start time at module import
_SERVER_START_TIME = time.monotonic()
_SERVER_START_UTC = datetime.now(timezone.utc)


def _get_memory_info() -> Dict[str, Any]:
    """Return process memory metrics using psutil if available, else os fallback."""
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        mem = proc.memory_info()
        return {
            "rss_mb": round(mem.rss / 1024 / 1024, 2),
            "vms_mb": round(mem.vms / 1024 / 1024, 2),
            "percent": round(proc.memory_percent(), 2),
        }
    except ImportError:
        # Fallback: minimal info without psutil
        return {
            "rss_mb": None,
            "vms_mb": None,
            "percent": None,
            "note": "Install psutil for detailed memory metrics",
        }


def _get_pool_status() -> Dict[str, Any]:
    """Read SQLAlchemy async engine pool stats (NullPool returns zeroes)."""
    pool = engine.pool
    try:
        return {
            "pool_class": type(pool).__name__,
            "checked_out": pool.checkedout(),
            "checked_in": pool.checkedin(),
            "overflow": pool.overflow(),
            "size": pool.size(),
        }
    except AttributeError:
        # NullPool / StaticPool don't expose these attrs
        return {
            "pool_class": type(pool).__name__,
            "note": "Pool metrics unavailable for this pool type",
        }


@router.get("/system")
async def system_health(db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """
    Extended system health endpoint returning:
    - Memory footprint
    - Server uptime
    - Signal counts from DB
    - DB connectivity and pool status
    - Paper trader + resolver status
    """
    # --- Uptime ---
    uptime_secs = time.monotonic() - _SERVER_START_TIME
    uptime_str = _format_uptime(uptime_secs)

    # --- Memory ---
    memory = _get_memory_info()

    # --- DB connectivity + signal counts ---
    db_ok = False
    total_signals = 0
    pending_signals = 0
    resolved_signals = 0
    try:
        # Lightweight ping
        await db.execute(text("SELECT 1"))
        db_ok = True

        total_res = await db.execute(select(func.count()).select_from(Signal))
        total_signals = total_res.scalar_one()

        pending_res = await db.execute(
            select(func.count()).select_from(Signal).where(Signal.status == "PENDING")
        )
        pending_signals = pending_res.scalar_one()

        resolved_signals = total_signals - pending_signals
    except Exception as e:
        db_ok = False

    # --- Pool ---
    pool_status = _get_pool_status()

    # --- Paper Trader & Resolver status ---
    # Import lazily to avoid circular imports at module level
    try:
        from app.services.paper_trader import paper_trader
        trader_status = {
            "running": paper_trader.is_running,
            "total_generated_session": paper_trader.total_generated,
            "uptime_seconds": round(paper_trader.uptime_seconds, 1),
            "enabled_pairs": [
                f"{cfg['pair']}/{cfg['timeframe']}"
                for cfg in paper_trader.pairs
            ],
        }
    except Exception:
        trader_status = {"running": False, "error": "PaperTrader unavailable"}

    return {
        "status": "online" if db_ok else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "server_started_at": _SERVER_START_UTC.isoformat(),
        "uptime": uptime_str,
        "uptime_seconds": round(uptime_secs, 1),
        "memory": memory,
        "database": {
            "connected": db_ok,
            "url": _mask_db_url(settings.DATABASE_URL),
            "total_signals": total_signals,
            "pending_signals": pending_signals,
            "resolved_signals": resolved_signals,
            "pool": pool_status,
        },
        "paper_trader": trader_status,
        "resolver": {
            "interval_seconds": 10,
            "note": "Resolver loop running in background, checks every 10s",
        },
    }


@router.get("")
def basic_health() -> Dict[str, Any]:
    """Fast basic health check (no DB query)."""
    return {
        "status": "online",
        "system": settings.PROJECT_NAME,
        "uptime_seconds": round(time.monotonic() - _SERVER_START_TIME, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"


def _mask_db_url(url: str) -> str:
    """Hide credentials in DB URL for safe display."""
    if "@" in url:
        scheme_user, rest = url.split("@", 1)
        scheme = scheme_user.split("://")[0]
        return f"{scheme}://***@{rest}"
    return url

