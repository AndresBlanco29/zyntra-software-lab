# Zyntra Software Lab DEMO only — single web process.
# Celery runs eager in DEMO (CELERY_TASK_ALWAYS_EAGER=1). No LTG scheduler.
web: python manage.py migrate --noinput && python manage.py check_demo_isolation --require-demo && python manage.py collectstatic --noinput && gunicorn config.config.wsgi:application --bind 0.0.0.0:$PORT --timeout 600 --graceful-timeout 120
