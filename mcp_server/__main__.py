"""python3 -m mcp_server [--transport stdio|streamable-http] [--port N]

stdio is the default (what .mcp.json launches). For stdio the protocol owns fd 1,
and repo code print()s to stdout in a few places (db_backend's pool fallback,
the legacy-queue warning) — a stray line there corrupts the JSON-RPC stream. So
before importing anything from the repo, fd 1 is duplicated for the transport and
then pointed at stderr: every later print, Python or C level, lands on stderr.
"""
from __future__ import annotations

import argparse
import io
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _claim_stdout():
    """Return a text stream on the real stdout and send fd 1 to stderr."""
    saved = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return io.TextIOWrapper(os.fdopen(saved, "wb", buffering=0), encoding="utf-8",
                            write_through=True)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="mcp_server", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--allow-remote", action="store_true",
                   help="Permit --host other than 127.0.0.1 (the server has no auth).")
    p.add_argument("--read-only", action="store_true",
                   help="Register only the getters (for a model mid-translation).")
    p.add_argument("--admin-url", default="http://127.0.0.1:8000",
                   help="Admin server for job status/queue control (default: %(default)s)")
    args = p.parse_args(argv)

    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.allow_remote:
        p.error("refusing to bind a non-loopback host without --allow-remote: "
                "these tools write to the production database and have no auth")

    out = _claim_stdout() if args.transport == "stdio" else None

    # Logger writes translate.log relative to cwd, settings.json/.env are
    # resolved from the repo root.
    os.chdir(REPO_ROOT)
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)

    from mcp_server.server import build_server

    mcp = build_server(host=args.host, port=args.port, admin_url=args.admin_url,
                       read_only=args.read_only,
                       # Over HTTP every request stands alone: a restart of this
                       # service never strands a client's session.
                       stateless_http=args.transport == "streamable-http")

    if args.transport == "stdio":
        import anyio
        from mcp.server.stdio import stdio_server

        async def run():
            async with stdio_server(stdout=anyio.wrap_file(out)) as (read, write):
                await mcp._mcp_server.run(read, write,
                                          mcp._mcp_server.create_initialization_options())

        anyio.run(run)
    else:
        # A long-lived service: pay the DB start-up (imports, migrations check,
        # entity cache) now rather than inside the first caller's lookup.
        from mcp_server.deps import app
        app().ensure_db()
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
