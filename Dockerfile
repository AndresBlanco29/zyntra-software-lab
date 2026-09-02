FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gettext \
        libjpeg62-turbo \
        zlib1g \
        libffi8 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .

# Railway startCommand in railway.toml overrides this when set.
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py check_demo_isolation --require-demo && python manage.py collectstatic --noinput && gunicorn config.config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --timeout 600 --graceful-timeout 120"]
