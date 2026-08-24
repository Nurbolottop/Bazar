#!/usr/bin/env bash
#
# Инициализация нового проекта из скелета.
#
#   ./scripts/init-project.sh myproject
#
# Что делает:
#   - проверяет, что имя проекта ещё не занято на этом сервере
#   - подбирает свободные порты (web / postgres / redis)
#   - генерирует уникальные SECRET_KEY и пароль БД
#   - создаёт .env
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"

FORCE=0
PROJECT=""

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help)
      echo "Использование: $0 <имя-проекта> [--force]"
      exit 0
      ;;
    *) PROJECT="$arg" ;;
  esac
done

# ---------------------------------------------------------------------------
# Имя проекта
# ---------------------------------------------------------------------------
if [ -z "$PROJECT" ]; then
  read -r -p "Имя проекта (латиница, цифры, дефис): " PROJECT
fi

if ! echo "$PROJECT" | grep -Eq '^[a-z][a-z0-9-]{1,30}$'; then
  echo "Ошибка: имя должно начинаться с буквы и содержать только a-z, 0-9 и дефис." >&2
  exit 1
fi

# COMPOSE_PROJECT_NAME с дефисами валиден, но в именах БД дефис требует кавычек
DB_NAME="$(echo "$PROJECT" | tr '-' '_')"

# ---------------------------------------------------------------------------
# Проверка, что имя не занято другим проектом на этом сервере
# ---------------------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
  if docker volume ls --format '{{.Name}}' 2>/dev/null | grep -q "^${PROJECT}_"; then
    echo "Ошибка: на сервере уже есть volume проекта '${PROJECT}'." >&2
    echo "Выбери другое имя, иначе проекты будут делить данные." >&2
    exit 1
  fi
  if docker ps -a --format '{{.Label "com.docker.compose.project"}}' 2>/dev/null | grep -qx "$PROJECT"; then
    echo "Ошибка: на сервере уже есть контейнеры проекта '${PROJECT}'." >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# Подбор свободных портов
# ---------------------------------------------------------------------------
# Порты, уже расписанные в .env соседних проектов на этом сервере.
# Нужно потому, что не запущенный проект не занимает сокет, но его порты
# всё равно зарезервированы — иначе два проекта получат одинаковые.
scan_reserved_ports() {
  local parent this
  parent="$(dirname "$ROOT")"
  this="$(cd "$(dirname "$ENV_FILE")" 2>/dev/null && pwd)/$(basename "$ENV_FILE")"

  for f in "$parent"/*/.env; do
    [ -f "$f" ] || continue
    [ "$f" = "$this" ] && continue
    grep -hE '^[[:space:]]*(WEB_PORT|DB_PORT|REDIS_PORT)=' "$f" 2>/dev/null \
      | cut -d= -f2 | sed 's/[[:space:]]//g' | grep -E '^[0-9]+$' || true
  done
}

RESERVED_PORTS=" $(scan_reserved_ports | sort -un | tr '\n' ' ') "
if [ -n "$(echo "$RESERVED_PORTS" | tr -d ' ')" ]; then
  echo "Заняты соседними проектами:$RESERVED_PORTS"
fi

port_in_use() {
  local port="$1"

  # Занят соседним проектом из того же каталога
  case "$RESERVED_PORTS" in
    *" $port "*) return 0 ;;
  esac

  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | grep -Eq "[:.]${port}[[:space:]]" && return 0
  elif command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && return 0
  elif command -v netstat >/dev/null 2>&1; then
    netstat -ltn 2>/dev/null | grep -Eq "[:.]${port}[[:space:]]" && return 0
  fi

  # Порты уже описанных проектов, даже если контейнеры сейчас остановлены
  if command -v docker >/dev/null 2>&1; then
    docker ps -a --format '{{.Ports}}' 2>/dev/null | grep -q ":${port}->" && return 0
  fi

  return 1
}

find_free_port() {
  local port="$1" limit="$2"
  while [ "$port" -le "$limit" ]; do
    if ! port_in_use "$port"; then
      echo "$port"
      return 0
    fi
    port=$((port + 1))
  done
  echo "Ошибка: не нашёл свободный порт в диапазоне до ${limit}." >&2
  exit 1
}

echo "Подбираю свободные порты..."
WEB_PORT="$(find_free_port 8080 8199)"
DB_PORT="$(find_free_port 5433 5499)"
REDIS_PORT="$(find_free_port 6380 6499)"

# ---------------------------------------------------------------------------
# Секреты
# ---------------------------------------------------------------------------
gen_secret() {
  local len="$1"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 $((len * 2)) | tr -d '\n=+/' | cut -c1-"$len"
  else
    LC_ALL=C tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c "$len"
  fi
}

SECRET_KEY="$(gen_secret 50)"
DB_PASSWORD="$(gen_secret 24)"

# ---------------------------------------------------------------------------
# Запись .env
# ---------------------------------------------------------------------------
if [ -f "$ENV_FILE" ] && [ "$FORCE" -eq 0 ]; then
  echo "Ошибка: $ENV_FILE уже существует." >&2
  echo "Удали его или запусти с --force, если точно хочешь перезаписать." >&2
  exit 1
fi

cat > "$ENV_FILE" <<EOF
# Сгенерировано scripts/init-project.sh

# Имя проекта. Префикс для контейнеров, volume и сети.
COMPOSE_PROJECT_NAME=${PROJECT}

# Порты на хосте (подобраны как свободные на момент инициализации)
WEB_PORT=${WEB_PORT}
DB_PORT=${DB_PORT}
REDIS_PORT=${REDIS_PORT}

# Django
SECRET_KEY=${SECRET_KEY}

# Домены. ОБЯЗАТЕЛЬНО заменить на свои перед деплоем.
ALLOWED_HOSTS=127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=http://127.0.0.1,http://localhost

LANGUAGE_CODE=ru
TIME_ZONE=Asia/Bishkek

# База данных
POSTGRES_DB=${DB_NAME}
POSTGRES_USER=${DB_NAME}_user
POSTGRES_PASSWORD=${DB_PASSWORD}
POSTGRES_HOST=db
POSTGRES_PORT=5432

# Redis
REDIS_URL=redis://redis:6379/0
EOF

chmod 600 "$ENV_FILE"

cat <<EOF

Готово. Создан ${ENV_FILE}

  Проект:     ${PROJECT}
  Контейнеры: ${PROJECT}-web-1, ${PROJECT}-db-1, ${PROJECT}-redis-1
  Volume:     ${PROJECT}_postgres_data
  Сеть:       ${PROJECT}_default
  База:       ${DB_NAME} (пользователь ${DB_NAME}_user)

  Django:     127.0.0.1:${WEB_PORT}
  Postgres:   127.0.0.1:${DB_PORT}
  Redis:      127.0.0.1:${REDIS_PORT}

Осталось: прописать ALLOWED_HOSTS и CSRF_TRUSTED_ORIGINS под свой домен.

Запуск:
  docker compose up --build                              # dev
  docker compose -f docker-compose.prod.yml up -d --build # prod
EOF
