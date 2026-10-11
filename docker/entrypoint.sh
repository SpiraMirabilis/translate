#!/bin/sh
# Container entrypoint: prepare /data, drop to the t9 user, start a T9 process.
#   admin   (default) full app: admin UI + API + public reader, port 8000
#   public  public reader only (web/public_app.py), port 8000
#   <cmd>   anything else runs as-is, e.g. `python translator.py --list-books`
set -e

DATA=/data

if [ "$(id -u)" = "0" ]; then
    # A bind-mounted ./data is created root-owned by the Docker daemon; hand
    # the top level (and the directories below) to t9. Existing files keep
    # their owner, so this is cheap on every start.
    mkdir -p "$DATA"
    for d in covers illustrations epub_cache output logs sitemaps; do
        mkdir -p "$DATA/$d"
    done
    chown t9:t9 "$DATA"
    for d in covers illustrations epub_cache output logs sitemaps; do
        chown t9:t9 "$DATA/$d"
    done
    [ -e "$DATA/.env" ] || { touch "$DATA/.env"; chown t9:t9 "$DATA/.env"; chmod 600 "$DATA/.env"; }
    exec setpriv --reuid=t9 --regid=t9 --init-groups "$0" "$@"
fi

if [ -z "$T9_PASSWORD" ] && ! grep -q '^T9_PASSWORD=.' "$DATA/.env" 2>/dev/null; then
    echo "WARNING: T9_PASSWORD is not set — the admin UI is open to anyone who can reach the port." >&2
    echo "         Set it in data/.env before exposing the container beyond this machine." >&2
fi

# Trust X-Forwarded-* / CF-Connecting-IP only from the compose network gateway
# (where a reverse proxy on the host appears to connect from) and loopback.
FORWARDED_ALLOW="${T9_FORWARDED_ALLOW_IPS:-127.0.0.1}"

case "${1:-admin}" in
    admin)  [ $# -gt 0 ] && shift; APP=web.app:app ;;
    public) shift; APP=web.public_app:app ;;
    *)      exec "$@" ;;
esac

exec python -m uvicorn "$APP" --host 0.0.0.0 --port 8000 \
    --proxy-headers --forwarded-allow-ips="$FORWARDED_ALLOW" "$@"
