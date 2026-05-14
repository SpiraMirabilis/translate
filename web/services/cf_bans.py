"""
Cloudflare user-firewall ban client (pure-Python port of ~/scripts/cf-ban.sh).

Uses httpx + ipaddress to add/remove block rules at the Cloudflare edge.
Requires CF_API_EMAIL and CF_API_KEY (Global API Key) in the project's
.env. Replaces the subprocess-to-bash approach with in-process async
calls so admin clicks don't fork shells, and the logic is easier to
test.

Cloudflare's user-level firewall ip_range targets only accept /16 or
/24 (IPv4) and /32, /48, or /64 (IPv6). Narrower CIDRs are widened to
the next allowed prefix; wider CIDRs raise ValueError.
"""

import ipaddress
import logging
import os
from typing import Optional

import httpx

CF_BASE = "https://api.cloudflare.com/client/v4/user/firewall/access_rules/rules"
TIMEOUT = 15.0

logger = logging.getLogger(__name__)


def _auth_headers() -> Optional[dict]:
    email = os.getenv("CF_API_EMAIL", "").strip()
    key = os.getenv("CF_API_KEY", "").strip()
    if not email or not key:
        return None
    return {
        "X-Auth-Email": email,
        "X-Auth-Key": key,
        "Content-Type": "application/json",
    }


def resolve_target(input_str: str) -> tuple[str, str]:
    """
    Map an IP or CIDR to Cloudflare's (target, value) form.

      plain IPv4         → ('ip',       '1.2.3.4')
      plain IPv6         → ('ip6',      '2001:db8::1')
      IPv4 /32           → ('ip',       <addr>)
      IPv6 /128          → ('ip6',      <addr>)
      IPv4 CIDR (>=/24)  → ('ip_range', widened to /16 or /24)
      IPv6 CIDR (>=/64)  → ('ip_range', widened to /32 / /48 / /64)

    Raises ValueError if the prefix is wider than CF's minimum.
    """
    s = input_str.strip()
    if "/" not in s:
        if ":" in s:
            ipaddress.IPv6Address(s)  # validates
            return ("ip6", s)
        ipaddress.IPv4Address(s)
        return ("ip", s)

    net = ipaddress.ip_network(s, strict=False)
    if isinstance(net, ipaddress.IPv4Network):
        if net.prefixlen == 32:
            return ("ip", str(net.network_address))
        allowed = (16, 24)
    else:
        if net.prefixlen == 128:
            return ("ip6", str(net.network_address))
        allowed = (32, 48, 64)

    candidates = [a for a in allowed if a <= net.prefixlen]
    if not candidates:
        raise ValueError(
            f"/{net.prefixlen} is wider than Cloudflare's minimum /{allowed[0]}"
        )
    new_p = max(candidates)
    widened = ipaddress.ip_network(f"{net.network_address}/{new_p}", strict=False)
    return ("ip_range", str(widened))


async def push_ban(ip_or_cidr: str, note: str = "comment-spam") -> tuple[bool, str]:
    """Add a CF block rule. Returns (ok, message)."""
    headers = _auth_headers()
    if headers is None:
        return (False, "CF_API_EMAIL/CF_API_KEY not configured")
    try:
        target, value = resolve_target(ip_or_cidr)
    except ValueError as e:
        return (False, str(e))
    payload = {
        "mode": "block",
        "configuration": {"target": target, "value": value},
        "notes": note[:1024],
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(CF_BASE, headers=headers, json=payload)
            if resp.status_code >= 400:
                return (False, f"{resp.status_code}: {resp.text[:200]}")
            return (True, "ok")
    except httpx.HTTPError as e:
        return (False, f"network: {e}")


async def remove_ban(ip_or_cidr: str) -> tuple[bool, str]:
    """Remove a CF block rule (idempotent — absent rule is not an error)."""
    headers = _auth_headers()
    if headers is None:
        return (False, "CF_API_EMAIL/CF_API_KEY not configured")
    try:
        target, value = resolve_target(ip_or_cidr)
    except ValueError as e:
        return (False, str(e))
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(CF_BASE, headers=headers, params={
                "mode": "block",
                "configuration.target": target,
                "configuration.value": value,
                "per_page": "1",
            })
            if r.status_code >= 400:
                return (False, f"lookup {r.status_code}: {r.text[:200]}")
            results = (r.json().get("result") or [])
            if not results:
                return (True, "no matching rule")
            rule_id = results[0]["id"]
            d = await client.delete(f"{CF_BASE}/{rule_id}", headers=headers)
            if d.status_code >= 400:
                return (False, f"delete {d.status_code}: {d.text[:200]}")
            return (True, "ok")
    except httpx.HTTPError as e:
        return (False, f"network: {e}")


async def list_bans() -> list[dict]:
    """Fetch all block rules from Cloudflare. The DB is canonical; this is for reconciliation."""
    headers = _auth_headers()
    if headers is None:
        return []
    rules: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            page = 1
            while True:
                r = await client.get(CF_BASE, headers=headers, params={
                    "mode": "block", "page": str(page), "per_page": "100",
                })
                if r.status_code >= 400:
                    logger.warning("cf list_bans %s: %s", r.status_code, r.text[:200])
                    break
                j = r.json()
                rules.extend(j.get("result") or [])
                total_pages = (j.get("result_info") or {}).get("total_pages", 1)
                if page >= total_pages:
                    break
                page += 1
    except httpx.HTTPError as e:
        logger.warning("cf list_bans network error: %s", e)
    return rules
