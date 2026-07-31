web: python manage.py migrate --noinput && python manage.py ensure_superuser && python config/core/fill_spanish_catalog.py && python manage.py seed_commercial_assistant_knowledge && python manage.py compilemessages --ignore=.venv --ignore=venv && python manage.py collectstatic --noinput && gunicorn config.config.wsgi:application --bind 0.0.0.0:$PORT --timeout 600 --graceful-timeout 120
scheduler: python manage.py run_production_scheduler_daemon
worker: celery -A config.config worker --loglevel=INFO --concurrency=2
beat: celery -A config.config beat --loglevel=INFO