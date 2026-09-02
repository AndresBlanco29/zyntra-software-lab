#!/bin/sh
set -e
echo "[demo] migrate..."
python manage.py migrate --noinput
echo "[demo] isolation check (non-fatal)..."
python manage.py check_demo_isolation --require-demo || echo "[demo] WARN: isolation check failed — check Railway variables"
echo "[demo] collectstatic..."
python manage.py collectstatic --noinput
echo "[demo] starting gunicorn on PORT=${PORT}"
exec gunicorn config.config.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --timeout 600 --graceful-timeout 120 --access-logfile - --error-logfile -
