FROM node:24.14.0-bookworm-slim AS assets
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY assets/ assets/
COPY templates/ templates/
RUN npm run build

FROM python:3.12.16-slim-bookworm AS translations
WORKDIR /build
RUN pip install --no-cache-dir Babel==2.17.0
COPY locale/ locale/
RUN pybabel compile -D django -d locale

FROM python:3.12.16-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_SETTINGS_MODULE=config.settings
WORKDIR /app
RUN groupadd --system --gid 10001 erp \
    && useradd --system --uid 10001 --gid erp --home-dir /app --shell /usr/sbin/nologin erp \
    && mkdir /app/staticfiles /app/run \
    && chown erp:erp /app/staticfiles /app/run
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/ apps/
COPY bot/ bot/
COPY config/ config/
COPY templates/ templates/
COPY static/ static/
COPY scripts/health_probe.py scripts/health_probe.py
COPY manage.py ./
COPY --from=assets /build/static/ static/
COPY --from=translations /build/locale/ locale/
USER erp
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--forwarded-allow-ips", ""]
