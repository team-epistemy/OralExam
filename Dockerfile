# Epistemy M3 — single image, two entrypoints (serve / worker).
FROM public.ecr.aws/docker/library/python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# System libs needed by psycopg2 and python-pptx (lxml).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 libxml2 libxslt1.1 && rm -rf /var/lib/apt/lists/*

COPY requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements-runtime.txt

COPY backend ./backend

EXPOSE 8080
ENTRYPOINT ["python", "-m", "backend.app.main"]
CMD ["serve"]
