#!/bin/bash
# Recover a t9 MySQL dump from the private backup bucket.
#
#   ./restore_mysql.sh --list
#   ./restore_mysql.sh --fetch latest [--dest DIR]
#   ./restore_mysql.sh --fetch t9-20260906-033002.sql.gz --dest /tmp
#   ./restore_mysql.sh --restore latest --yes-really
#
# --fetch downloads and verifies the sha256 recorded on the object at upload
# time. --restore does that and then loads the dump into the live database,
# which DESTROYS current data — hence --yes-really.
#
# The restore runs from this VM because the t9 MySQL grant is host-restricted.

set -euo pipefail

PROJECT_DIR="/home/mdm/t9"
ENV_FILE="$PROJECT_DIR/.env"
SPACES_HELPER="$PROJECT_DIR/backup_spaces.py"
DEST="$PROJECT_DIR/backups"

MODE=""
NAME=""
YES_REALLY=0

usage() {
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-1}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --list)      MODE="list"; shift ;;
        --fetch)     MODE="fetch";   NAME="${2:-}"; shift 2 ;;
        --restore)   MODE="restore"; NAME="${2:-}"; shift 2 ;;
        --dest)      DEST="${2:-}"; shift 2 ;;
        --yes-really) YES_REALLY=1; shift ;;
        -h|--help)   usage 0 ;;
        *) echo "unknown argument: $1" >&2; usage ;;
    esac
done

[[ -n "$MODE" ]] || usage

if [[ "$MODE" == "list" ]]; then
    exec python3 "$SPACES_HELPER" list
fi

[[ -n "$NAME" ]] || { echo "--$MODE needs a backup name or 'latest'" >&2; exit 1; }

if [[ "$MODE" == "restore" && "$YES_REALLY" -ne 1 ]]; then
    cat >&2 <<'WARN'
Refusing to restore without --yes-really.

This overwrites the live t9 database with the contents of the dump. Every
chapter, entity, comment and revision written since that dump was taken will
be lost. If you only want the file, use --fetch.
WARN
    exit 1
fi

echo "Fetching $NAME ..."
# The helper verifies the sha256 and emits "PATH=<file>" as its final line.
FETCH_OUT=$(python3 "$SPACES_HELPER" fetch "$NAME" --dest "$DEST" | tee /dev/stderr)
FILE=$(printf '%s\n' "$FETCH_OUT" | sed -n 's/^PATH=//p' | tail -n1)

[[ -n "$FILE" && -r "$FILE" ]] || { echo "fetch did not produce a readable file" >&2; exit 1; }

echo "Checking gzip integrity of $FILE ..."
gzip -t "$FILE"
echo "gzip OK"

if [[ "$MODE" == "fetch" ]]; then
    echo "Fetched and verified: $FILE"
    exit 0
fi

MYSQL_USER=$(grep -E '^MYSQL_USER=' "$ENV_FILE" | head -n1 | cut -d= -f2-)
MYSQL_PASS=$(grep -E '^MYSQL_PASS=' "$ENV_FILE" | head -n1 | cut -d= -f2-)
MYSQL_HOST=$(grep -E '^MYSQL_HOST=' "$ENV_FILE" | head -n1 | cut -d= -f2-)
MYSQL_DB=$(grep -E '^MYSQL_DB='   "$ENV_FILE" | head -n1 | cut -d= -f2-)

echo "Restoring $FILE into ${MYSQL_DB}@${MYSQL_HOST} ..."
echo "Stop the app first if it is running: systemctl --user stop t9.service t9-public.service"
zcat "$FILE" | MYSQL_PWD="$MYSQL_PASS" mysql \
    -h "$MYSQL_HOST" -u "$MYSQL_USER" --default-character-set=utf8mb4 "$MYSQL_DB"
echo "Restore complete."
