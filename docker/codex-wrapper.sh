#!/bin/sh
# /usr/local/bin/codex in the image. `docker compose exec` runs as root and
# skips the entrypoint; run as root, codex would leave root-owned files in
# CODEX_HOME — which may be the host's own ~/.codex, bind-mounted. So as
# root, run it as whoever owns CODEX_HOME (the same user the app runs as).
BIN=/usr/local/lib/codex/codex
if [ "$(id -u)" = "0" ]; then
    uid=$(stat -c %u "${CODEX_HOME:-/data/codex}" 2>/dev/null || echo 0)
    gid=$(stat -c %g "${CODEX_HOME:-/data/codex}" 2>/dev/null || echo 0)
    if [ "$uid" = "0" ]; then
        uid=$(id -u t9); gid=$(id -g t9)
    fi
    [ "$uid" = "$(id -u t9)" ] && home=/home/t9 || home=/tmp/t9-home
    mkdir -p "$home" && chown "$uid:$gid" "$home"
    exec setpriv --reuid="$uid" --regid="$gid" --clear-groups env HOME="$home" "$BIN" "$@"
fi
exec "$BIN" "$@"
