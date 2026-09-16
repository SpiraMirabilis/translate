"""
Start the T9 web GUI server.
Run from the project root: python3 run_web.py
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "web.app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=["."],
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
