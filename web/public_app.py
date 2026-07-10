"""
Public reader FastAPI application — entry point.

Run with:
    python web/public_app.py
or:
    uvicorn web.public_app:app --host 127.0.0.1 --port 8001

Contains ONLY the unauthenticated public surface: /api/public/*,
/api/health, /simple, and the Library/Reader SPA routes + static assets.
No admin routes, auth, translation engine, or WebSocket exist in this
process — it is safe to reverse-proxy wholesale, and heavy work in the
admin process (port 8000) cannot slow it down. See web/app_factory.py.
"""
import sys
import os

# Make the project root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.app_factory import create_app

app = create_app(public_only=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "web.public_app:app",
        host="127.0.0.1",
        port=8001,
        reload=True,
        app_dir=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
