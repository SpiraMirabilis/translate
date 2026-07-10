#!/usr/bin/env python3
"""
T9 Service Watchdog

Polls the /api/health endpoint of each T9 service (admin app on :8000,
public reader app on :8001) and recovers them independently:

1. After FAIL_THRESHOLD consecutive failures on one service, restart just
   that systemd user service (`systemctl --user restart <unit>`).
2. If a service stays unhealthy through REBOOT_AFTER_RESTARTS consecutive
   restarts (i.e. restarting the process didn't fix it — a system-level
   wedge), escalate to a full VM reboot, the original last-resort behavior.

A unit that is cleanly `systemctl --user stop`ped (state "inactive") is
skipped — the watchdog never fights a manual stop.

Best practices implemented:
- Consecutive failure threshold (default 3) to avoid restarting on transient blips
- Cooldown period after a restart to let the service stabilise
- Exponential backoff on repeated restarts to avoid restart loops
- Logs to stdout/stderr for journalctl visibility
- Configurable via environment variables

Install as a systemd user service:
    cp deploy/t9-watchdog.service ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now t9-watchdog

Environment variables:
    T9_WATCHDOG_TARGETS  Semicolon list of unit=health_url pairs
                         (default: t9.service=http://127.0.0.1:8000/api/health;
                                   t9-public.service=http://127.0.0.1:8001/api/health)
    T9_POLL_INTERVAL     Seconds between polls              (default: 30)
    T9_FAIL_THRESHOLD    Consecutive fails to act           (default: 3)
    T9_RESTART_COOLDOWN  Base cooldown after a restart      (default: 60)
    T9_MAX_COOLDOWN      Max cooldown (backoff cap)         (default: 300)
    T9_REBOOT_AFTER_RESTARTS  Failed restarts before reboot (default: 2)
"""

import json
import logging
import logging.handlers
import os
import subprocess
import sys
import time
import urllib.request

# ── Configuration ────────────────────────────────────────────────────

DEFAULT_TARGETS = (
    "t9.service=http://127.0.0.1:8000/api/health;"
    "t9-public.service=http://127.0.0.1:8001/api/health"
)


def _parse_targets(spec: str) -> dict:
    """'unit=url;unit=url' -> {unit: url}."""
    out = {}
    for pair in spec.split(";"):
        pair = pair.strip()
        if not pair:
            continue
        unit, _, url = pair.partition("=")
        if not unit or not url:
            raise ValueError(f"Bad T9_WATCHDOG_TARGETS entry: {pair!r}")
        out[unit.strip()] = url.strip()
    if not out:
        raise ValueError("T9_WATCHDOG_TARGETS is empty")
    return out


TARGETS = _parse_targets(os.environ.get("T9_WATCHDOG_TARGETS", DEFAULT_TARGETS))
POLL_INTERVAL = int(os.environ.get("T9_POLL_INTERVAL", "30"))
FAIL_THRESHOLD = int(os.environ.get("T9_FAIL_THRESHOLD", "3"))
RESTART_COOLDOWN = int(os.environ.get("T9_RESTART_COOLDOWN", "60"))
MAX_COOLDOWN = int(os.environ.get("T9_MAX_COOLDOWN", "300"))
REBOOT_AFTER_RESTARTS = int(os.environ.get("T9_REBOOT_AFTER_RESTARTS", "2"))
# Persistent log file so we can confirm *whether the watchdog ever actually
# reboots the box* (and capture the resource state at the moment it does).
# journald is volatile across some reboots; a file on disk is not.
LOG_FILE = os.environ.get(
    "T9_WATCHDOG_LOG",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "t9_watchdog.log"),
)

# ── Logging ──────────────────────────────────────────────────────────

_handlers = [logging.StreamHandler(sys.stdout)]
try:
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    _handlers.append(
        logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
    )
except OSError as _e:
    print(f"[watchdog] could not open log file {LOG_FILE}: {_e}", file=sys.stderr)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("t9-watchdog")


def _resource_snapshot() -> str:
    """Best-effort one-line memory + load summary for diagnostics, so a
    failure logged here can be correlated with memory pressure."""
    parts = []
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                k, _, v = line.partition(":")
                mem[k] = v.strip()
        parts.append(
            f"MemAvailable={mem.get('MemAvailable', '?')} "
            f"SwapFree={mem.get('SwapFree', '?')}"
        )
    except OSError:
        pass
    try:
        parts.append(f"loadavg={os.getloadavg()[0]:.2f}")
    except OSError:
        pass
    return " ".join(parts) or "(unavailable)"

# ── Helpers ──────────────────────────────────────────────────────────


def check_health(url: str) -> bool:
    """Return True if the health endpoint reports healthy."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("status") == "healthy"
    except Exception as e:
        log.warning("Health check failed for %s: %s", url, e)
        return False


def unit_state(unit: str) -> str:
    """systemctl --user is-active output ('active', 'inactive', 'failed', ...)."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True, text=True, timeout=15,
        )
        return result.stdout.strip() or "unknown"
    except Exception as e:
        log.warning("Could not query state of %s: %s", unit, e)
        return "unknown"


def restart_unit(unit: str) -> bool:
    log.warning(
        "Restarting %s (systemctl --user restart) — resources: %s",
        unit, _resource_snapshot(),
    )
    try:
        result = subprocess.run(
            ["systemctl", "--user", "restart", unit],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as e:
        log.error("Restart of %s failed: %s", unit, e)
        return False
    if result.returncode == 0:
        log.info("%s restarted successfully", unit)
        return True
    log.error("Failed to restart %s: %s", unit, result.stderr.strip())
    return False


def reboot_vm(reason: str):
    """Last resort: service restarts didn't recover it — system-level wedge."""
    log.critical(
        "WATCHDOG TRIGGERING FULL VM REBOOT (sudo reboot) — %s — resources: %s",
        reason, _resource_snapshot(),
    )
    # Flush handlers before the box goes down so the reboot line is on disk.
    for h in logging.getLogger().handlers:
        try:
            h.flush()
        except Exception:
            pass
    result = subprocess.run(
        ["sudo", "reboot"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("Reboot command failed: %s", result.stderr.strip())


# ── Main loop ────────────────────────────────────────────────────────


class TargetState:
    def __init__(self):
        self.consecutive_failures = 0
        self.restarts_without_recovery = 0
        self.cooldown_until = 0.0


def main():
    states = {unit: TargetState() for unit in TARGETS}

    log.info(
        "Watchdog started — polling %s every %ds, threshold %d failures, "
        "reboot after %d failed restarts",
        ", ".join(f"{u} ({url})" for u, url in TARGETS.items()),
        POLL_INTERVAL,
        FAIL_THRESHOLD,
        REBOOT_AFTER_RESTARTS,
    )

    while True:
        now = time.monotonic()
        for unit, url in TARGETS.items():
            st = states[unit]
            if now < st.cooldown_until:
                continue  # let a just-restarted service stabilise

            if check_health(url):
                if st.consecutive_failures > 0:
                    log.info("%s recovered after %d failure(s)", unit, st.consecutive_failures)
                st.consecutive_failures = 0
                st.restarts_without_recovery = 0
                continue

            st.consecutive_failures += 1
            log.warning(
                "%s unhealthy (%d/%d consecutive failures) — resources: %s",
                unit, st.consecutive_failures, FAIL_THRESHOLD, _resource_snapshot(),
            )
            if st.consecutive_failures < FAIL_THRESHOLD:
                continue

            state = unit_state(unit)
            if state == "inactive":
                # Cleanly stopped by hand — never fight a manual stop.
                log.info("%s is inactive (manually stopped?) — skipping recovery", unit)
                st.consecutive_failures = 0
                st.restarts_without_recovery = 0
                continue

            if st.restarts_without_recovery >= REBOOT_AFTER_RESTARTS:
                reboot_vm(f"{unit} still unhealthy after "
                          f"{st.restarts_without_recovery} restart(s)")
                # If the reboot command failed we fall through and keep trying.
                st.consecutive_failures = 0
                st.cooldown_until = time.monotonic() + MAX_COOLDOWN
                continue

            restart_unit(unit)
            st.restarts_without_recovery += 1
            st.consecutive_failures = 0
            # Exponential backoff: base * 2^(restarts-1), capped
            cooldown = min(
                RESTART_COOLDOWN * (2 ** (st.restarts_without_recovery - 1)),
                MAX_COOLDOWN,
            )
            st.cooldown_until = time.monotonic() + cooldown
            log.info("Cooling down %s for %ds before resuming polls", unit, cooldown)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
