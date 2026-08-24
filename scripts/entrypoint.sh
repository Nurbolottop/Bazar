#!/bin/sh
set -e

# Ожидание доступности базы данных.
# depends_on: service_healthy обычно уже гарантирует готовность,
# но проверка нужна при запуске без compose.
echo "Ожидание доступности базы данных ${POSTGRES_HOST}:${POSTGRES_PORT}..."
until nc -z -w 2 "$POSTGRES_HOST" "$POSTGRES_PORT"
do
  echo "Waiting for PostgreSQL database connection..."
  sleep 1
done

echo "База данных доступна. Применяем миграции..."
# makemigrations здесь намеренно нет: миграции хранятся в репозитории
# и создаются разработчиком, а не генерируются на сервере.
python manage.py migrate --noinput

echo "Собираем статические файлы..."
python manage.py collectstatic --noinput

# Запускаем переданную команду (если она есть)
exec "$@"
