#!/bin/sh
# Container entrypoint: prepare /data, drop root, start a T9 process.
#   admin   (default) full app: admin UI + API + public reader, port 8000
#   public  public reader only (web/public_app.py), port 8000
#   <cmd>   anything else runs as-is, e.g. `python translator.py --list-books`
set -e

DATA=/data
SUBDIRS="covers illustrations epub_cache output logs sitemaps"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA"
    for d in $SUBDIRS codex; do mkdir -p "$DATA/$d"; done

    # Run as the host user who owns the mounted state, so files in ./data and
    # a bind-mounted ~/.codex stay theirs: PUID/PGID if given, else the owner
    # of the Codex login dir or of /data (whichever isn't root), else 1000.
    # Directories the Docker daemon created for a bind mount are root-owned,
    # which is why root never counts as "the owner".
    owner() { stat -c "$1" "$2"; }
    if [ -n "$PUID" ]; then
        uid=$PUID; gid=${PGID:-$PUID}
    elif [ "$(owner %u "$DATA/codex")" != "0" ]; then
        uid=$(owner %u "$DATA/codex"); gid=$(owner %g "$DATA/codex")
    elif [ "$(owner %u "$DATA")" != "0" ]; then
        uid=$(owner %u "$DATA"); gid=$(owner %g "$DATA")
    else
        uid=$(id -u t9); gid=$(id -g t9)
    fi

    # Take over only what is root-owned (i.e. just created); never re-own an
    # existing directory, which may be the user's own ~/.codex.
    for p in "$DATA" $(for d in $SUBDIRS codex; do echo "$DATA/$d"; done); do
        [ "$(owner %u "$p")" = "0" ] && chown "$uid:$gid" "$p"
    done
    [ "$(owner %a "$DATA/codex")" = "700" ] || [ "$(owner %u "$DATA/codex")" != "$uid" ] || chmod 700 "$DATA/codex"
    if [ ! -e "$DATA/.env" ]; then
        touch "$DATA/.env" && chown "$uid:$gid" "$DATA/.env" && chmod 600 "$DATA/.env"
    fi
    if [ "$(owner %u "$DATA/codex")" != "$uid" ]; then
        echo "WARNING: $DATA/codex is owned by uid $(owner %u "$DATA/codex") but T9 runs as uid $uid;" >&2
        echo "         the codex provider won't be able to use the login there." >&2
    fi

    # The uid may have no passwd entry, so HOME can't come from one.
    if [ "$uid" = "$(id -u t9)" ]; then
        export HOME=/home/t9
    else
        export HOME=/tmp/t9-home
        mkdir -p "$HOME" && chown "$uid:$gid" "$HOME"
    fi
    exec setpriv --reuid="$uid" --regid="$gid" --clear-groups "$0" "$@"
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
