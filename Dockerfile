FROM python:3.12.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system ife && adduser --system --ingroup ife ife

COPY pyproject.toml ./
COPY apps ./apps
COPY config ./config
RUN pip install --no-cache-dir .

COPY . .
RUN DJANGO_DEBUG=false \
    DJANGO_SECRET_KEY=container-build-only \
    python manage.py collectstatic --noinput

USER ife

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
