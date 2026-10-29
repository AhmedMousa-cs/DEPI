"""Run with: python -m app.worker"""
import logging
import signal
import time

import app.services.job_handlers  # noqa: F401  (registers handlers)
from app.config import settings
from app.services.jobs import process_one, recover_stale

log = logging.getLogger("nexus.worker")
_stop = False


def _request_stop(signum, frame) -> None:
    global _stop
    _stop = True
    log.info("stop requested; finishing the current job first")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    log.info("worker started")
    last_recovery = 0.0
    while not _stop:
        did_work = False
        try:
            if time.monotonic() - last_recovery >= settings.job_recovery_interval_seconds:
                n = recover_stale()
                last_recovery = time.monotonic()
                if n:
                    log.warning("recovered %d stale job(s)", n)
            did_work = process_one()
        except Exception:  # noqa: BLE001  (never let the loop die)
            log.exception("worker loop error")
        if not did_work:
            time.sleep(settings.worker_poll_seconds)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
