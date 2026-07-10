"""
Admin (full) FastAPI application — entry point.

Run with:
    python web/app.py
or:
    uvicorn web.app:app --reload --app-dir ..

The application itself is built in web/app_factory.py, shared with the
public reader process (web/public_app.py). This module builds the FULL
app: admin API + auth + translation machinery + the public surface.
"""
import sys
import os

# Make the project root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.app_factory import create_app  # re-exported for tests / callers

app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "web.app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        app_dir=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
