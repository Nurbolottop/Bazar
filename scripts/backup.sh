#!/usr/bin/env bash
#
# Ежедневная резервная копия базы и медиа-файлов (ТЗ-02 п. 11.3).
# Хранение не менее 30 дней. Запускается кроном на хосте:
#   0 3 * * * /root/Bazar/scripts/backup.sh >> /root/backups/bazar/backup.log 2>&1
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/root/backups/bazar}"
STAMP="$(date +%Y-%m-%d_%H%M)"
KEEP_DAYS=30

mkdir -p "$BACKUP_DIR"
cd "$ROOT"

# shellcheck disable=SC1091
set -a; source "$ROOT/.env"; set +a

echo "[$STAMP] pg_dump ${POSTGRES_DB}..."
docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$BACKUP_DIR/db_${STAMP}.sql.gz"

echo "[$STAMP] архив медиа..."
tar -czf "$BACKUP_DIR/media_${STAMP}.tar.gz" -C "$ROOT/app" media 2>/dev/null || true

find "$BACKUP_DIR" -name '*.gz' -mtime +$KEEP_DAYS -delete

echo "[$STAMP] готово: $(du -sh "$BACKUP_DIR" | cut -f1) в $BACKUP_DIR"
