#!/bin/bash
# Start the T9 web GUI (backend + frontend dev server)
# Run from the project root: ./start_web.sh

cd "$(dirname "$0")"

cleanup() {
    echo ""
    echo "Shutting down..."
    # Kill the process groups so child processes (uvicorn workers, vite) also die
    kill -- -$BACKEND_PID -$FRONTEND_PID 2>/dev/null
    wait 2>/dev/null
    exit 0
}

trap cleanup INT TERM

# Check dependencies
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found"
    exit 1
fi

if ! command -v npm &>/dev/null; then
    echo "Error: npm not found"
    exit 1
fi

if [ ! -d "web/frontend/node_modules" ]; then
    echo "Installing frontend dependencies..."
    (cd web/frontend && npm install)
fi

echo "Starting T9 Web GUI"
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:5173  (open this one)"
echo ""
echo "Press Ctrl+C to stop both servers."
echo ""

# Start backend in its own process group
set -m
python3 -m uvicorn web.app:app --host 127.0.0.1 --port 8000 --reload &
BACKEND_PID=$!

# Start frontend dev server in its own process group
(cd web/frontend && npx vite --clearScreen false) &
FRONTEND_PID=$!
set +m

# Wait forever — cleanup is triggered by the trap
wait
