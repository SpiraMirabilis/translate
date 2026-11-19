#!/bin/sh
# /usr/local/bin/agy in the image. agy keeps its sign-in and state under
# $HOME (~/.gemini, ~/.config/antigravity), so HOME points at AGY_HOME
# (data/agy) — the login survives container rebuilds, exactly like
# CODEX_HOME for codex.
#
# T9's provider runs each translation call with its own throwaway home holding
# a copy of the sign-in, and says so in AGY_CALL_HOME; that wins. Any other run
# (the sign-in itself, `agy models`, …) uses AGY_HOME directly and holds the
# lock T9 takes to save a refreshed token, so a call never saves over a sign-in
# made meanwhile (T9 skips its save while the lock is held).
#
# `docker compose exec` runs as root and skips the entrypoint; as root, run agy
# as whoever owns AGY_HOME so the files there never end up root-owned.
BIN=/usr/local/lib/agy/agy
AGY_HOME="${AGY_HOME:-/data/agy}"
RUN_HOME="${AGY_CALL_HOME:-$AGY_HOME}"
LOCK="$AGY_HOME/.gemini/antigravity-cli/.t9-login.lock"
unset XDG_CONFIG_HOME XDG_DATA_HOME XDG_STATE_HOME XDG_CACHE_HOME

# Prefix that runs a command as the sign-in owner (empty when not root).
DROP=""
if [ "$(id -u)" = "0" ]; then
    uid=$(stat -c %u "$AGY_HOME" 2>/dev/null || echo 0)
    gid=$(stat -c %g "$AGY_HOME" 2>/dev/null || echo 0)
    if [ "$uid" = "0" ]; then
        uid=$(id -u t9); gid=$(id -g t9)
    fi
    DROP="setpriv --reuid=$uid --regid=$gid --clear-groups"
    # A T9 call made as root (`docker compose exec t9 python …`) created its
    # private call dir as root; agy runs as the sign-in's owner, so hand that
    # dir over. Only a real (non-symlink) t9-agy-* dir directly inside the
    # temp dir Python used (AGY_CALL_TMP), compared after normalising. Without
    # the hand-off agy cannot read its own call dir, so anything else fails.
    if [ -n "$AGY_CALL_HOME" ]; then
        call_root=$(dirname "$AGY_CALL_HOME")
        tmp_dir="${AGY_CALL_TMP:-${TMPDIR:-/tmp}}"
        case "$(basename "$call_root")" in
            t9-agy-*) ok=1 ;;
            *) ok=0 ;;
        esac
        if [ "$ok" = 1 ] && [ "$(realpath -m "$(dirname "$call_root")")" = "$(realpath -m "$tmp_dir")" ] \
                && [ -d "$call_root" ] && [ ! -L "$call_root" ]; then
            chown -R "$uid:$gid" "$call_root" || {
                echo "agy wrapper: could not hand $call_root to uid $uid" >&2; exit 1; }
        else
            echo "agy wrapper: refusing to hand $call_root to uid $uid (not a T9 call dir in $tmp_dir)" >&2
            exit 1
        fi
    fi
fi

if [ -z "$AGY_CALL_HOME" ]; then
    $DROP mkdir -p "$(dirname "$LOCK")" || exit 1
    exec $DROP env HOME="$RUN_HOME" python /app/docker/agy-lock.py "$LOCK" "$BIN" "$@"
fi
exec $DROP env HOME="$RUN_HOME" "$BIN" "$@"
