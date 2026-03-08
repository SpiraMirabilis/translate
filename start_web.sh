#!/bin/bash
# Start the T9 web GUI (backend + frontend dev server)
# Run from the project root: ./start_web.sh

cd "$(dirname "$0")"

cleanup() {
    echo ""
    echo "Shutting down..."
    kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
    wait $BACKEND_PID $FRONTEND_PID 2>/dev/null
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

# Start backend
python3 -m uvicorn web.app:app --host 127.0.0.1 --port 8000 --reload 2>&1 | sed 's/^/[backend]  /' &
BACKEND_PID=$!

# Start frontend dev server
(cd web/frontend && npm run dev -- --clearScreen false) 2>&1 | sed 's/^/[frontend] /' &
FRONTEND_PID=$!

# Wait for either to exit
wait -n $BACKEND_PID $FRONTEND_PID 2>/dev/null

# If one died, kill the other
cleanup
