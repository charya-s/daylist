FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 daylist \
    && mkdir /data && chown daylist /data
COPY manage.py .
COPY daylist daylist
USER daylist
EXPOSE 8000
CMD ["sh", "-c", "umask 077; python manage.py migrate --noinput && exec gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 4 daylist.wsgi:application"]
