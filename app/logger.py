"""
BEES Clinical Audit Logger
──────────────────────────
Provides a structured, append-only, rotating file logger for all clinical
audit events (case creation, variant confirmation, VCF deletion, authentication).

Writes to /var/log/bees/audit.log (root-owned service deployment) or falls
back to ./logs/bees_audit.log (local development).

Format: ISO-8601 timestamp | CASE-ID | OPERATOR | ACTION | DETAIL
"""
import logging
import os
from logging.handlers import TimedRotatingFileHandler

_audit_logger = None

def get_audit_logger() -> logging.Logger:
    """
    Returns the singleton audit logger, creating it on first call.
    The logger writes to a dedicated append-only file and is never
    emitted to stdout to prevent PHI leakage into system journals.
    """
    global _audit_logger
    if _audit_logger is not None:
        return _audit_logger

    logger = logging.getLogger("bees.audit")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # Do not propagate to root logger / stdout

    # Determine log directory: prefer /var/log/bees, fall back to ./logs
    system_log_dir = "/var/log/bees"
    local_log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")

    log_dir = None
    for candidate in [system_log_dir, local_log_dir]:
        try:
            os.makedirs(candidate, exist_ok=True)
            # Test writability
            test_path = os.path.join(candidate, ".write_test")
            with open(test_path, "w") as f:
                f.write("")
            os.remove(test_path)
            log_dir = candidate
            break
        except (OSError, PermissionError):
            continue

    if log_dir is None:
        # Last resort: write alongside the app
        log_dir = local_log_dir
        os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, "bees_audit.log")

    handler = TimedRotatingFileHandler(
        filename=log_path,
        when="midnight",
        backupCount=90,       # 90 days retention
        encoding="utf-8",
        delay=False,
    )

    # Set restrictive file permissions (rw owner only)
    try:
        os.chmod(log_path, 0o640)
    except OSError:
        pass

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    _audit_logger = logger
    print(f"  ✓  Audit log writing to: {log_path}")
    return _audit_logger
