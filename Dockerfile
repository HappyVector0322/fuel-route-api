FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_DEBUG=0 \
    DJANGO_ALLOWED_HOSTS=* \
    DJANGO_DB_PATH=/app/var/db.sqlite3

WORKDIR /app

# Dependencies first, so code changes don't invalidate this layer.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN python manage.py collectstatic --noinput \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /app/var \
    && chown -R app:app /app/var \
    && chmod +x docker/entrypoint.sh

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4)"

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "60", \
     "--access-logfile", "-"]
