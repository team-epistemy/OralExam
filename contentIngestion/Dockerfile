# Epistemy M3 — single image, two entrypoints (serve / worker).
FROM public.ecr.aws/docker/library/python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# System libs needed by psycopg2 and python-pptx (lxml).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 libxml2 libxslt1.1 && rm -rf /var/lib/apt/lists/*

COPY requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements-runtime.txt

COPY epistemy_m3 ./epistemy_m3

EXPOSE 8080
ENTRYPOINT ["python", "-m", "epistemy_m3.app.main"]
CMD ["serve"]
