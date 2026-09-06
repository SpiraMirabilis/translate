#!/bin/bash
# Daily MySQL backup for the t9 database.
#
# Reads credentials from /home/mdm/t9/.env and writes a gzipped dump to
# /home/mdm/t9/backups/, then uploads it to a PRIVATE DigitalOcean Space.
# The dump runs from this VM because the t9 MySQL grant is host-restricted
# (t9@localhost on the db host is denied).
#
# Retention:
#   - backup bucket (BACKUP_BUCKET in .env): 14 daily + 6 monthly
#   - this VM:                               only the most recent dump
# Local dumps older than the newest are pruned ONLY after the newest has been
# uploaded AND verified; if the upload fails, everything is kept locally and
# the script exits nonzero.
#
# The db host no longer holds backups (it is the machine most likely to be lost
# with the database, and its disk is tight). See backup_spaces.py for the
# storage layer and restore_mysql.sh for recovery.

set -euo pipefail

PROJECT_DIR="/home/mdm/t9"
BACKUP_DIR="$PROJECT_DIR/backups"
ENV_FILE="$PROJECT_DIR/.env"
LOG_FILE="$BACKUP_DIR/backup.log"
SPACES_HELPER="$PROJECT_DIR/backup_spaces.py"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# Loud, greppable, and on stderr so cron mails it.
fail() {
    log "BACKUP FAILED: $*"
    echo "BACKUP FAILED: $*" >&2
    exit 1
}

if [[ ! -r "$ENV_FILE" ]]; then
    fail "cannot read $ENV_FILE"
fi

# Pull only the MYSQL_* keys from .env so we don't execute arbitrary lines.
MYSQL_USER=$(grep -E '^MYSQL_USER=' "$ENV_FILE" | head -n1 | cut -d= -f2-)
MYSQL_PASS=$(grep -E '^MYSQL_PASS=' "$ENV_FILE" | head -n1 | cut -d= -f2-)
MYSQL_HOST=$(grep -E '^MYSQL_HOST=' "$ENV_FILE" | head -n1 | cut -d= -f2-)
MYSQL_DB=$(grep -E '^MYSQL_DB='   "$ENV_FILE" | head -n1 | cut -d= -f2-)

if [[ -z "${MYSQL_USER:-}" || -z "${MYSQL_PASS:-}" || -z "${MYSQL_HOST:-}" || -z "${MYSQL_DB:-}" ]]; then
    fail "missing MYSQL_* values in $ENV_FILE"
fi

TS=$(date '+%Y%m%d-%H%M%S')
NAME="${MYSQL_DB}-${TS}.sql.gz"
OUT="$BACKUP_DIR/$NAME"
TMP="${OUT}.partial"

log "Starting backup -> $OUT"

if MYSQL_PWD="$MYSQL_PASS" mysqldump \
        -h "$MYSQL_HOST" \
        -u "$MYSQL_USER" \
        --single-transaction \
        --quick \
        --routines \
        --triggers \
        --events \
        --default-character-set=utf8mb4 \
        "$MYSQL_DB" \
        2>>"$LOG_FILE" \
    | gzip -9 > "$TMP"; then
    mv "$TMP" "$OUT"
    SIZE=$(du -h "$OUT" | cut -f1)
    log "Backup OK ($SIZE)"
else
    rm -f "$TMP"
    fail "mysqldump failed"
fi

# Upload to the private backup bucket. The helper only reports success after a
# HEAD confirms the object's byte count matches the local file, so a true here
# means the dump genuinely exists off-box.
if python3 "$SPACES_HELPER" upload "$OUT" >>"$LOG_FILE" 2>&1; then
    log "Uploaded $NAME to the backup bucket"
else
    fail "upload of $NAME to the backup bucket failed — keeping all local backups"
fi

# Bucket-side retention. A prune failure leaves extra objects in the bucket,
# which is harmless — it must NOT stop the local prune below, or the VM fills up.
if python3 "$SPACES_HELPER" prune >>"$LOG_FILE" 2>&1; then
    log "Bucket prune OK"
else
    log "WARNING: bucket prune failed (backups intact, extra objects retained)"
fi

# The newest dump is now safely in the bucket — keep only it locally.
LOCAL_DELETED=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name "${MYSQL_DB}-*.sql.gz" ! -name "$NAME" -print -delete | wc -l)
log "Local prune: $LOCAL_DELETED older file(s) removed, kept $NAME"
