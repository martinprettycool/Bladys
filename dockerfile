FROM python:3.14-slim

WORKDIR /app

# Instalar uv
RUN pip install --no-cache-dir uv

# Instalar dependencias con caché reutilizable
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Copiar únicamente el código del backend
COPY src/ ./src/

# Copiar las migraciones de base de datos
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Instalar el paquete Blady's
RUN uv sync --frozen --no-dev

# Crear grupo y usuario sin privilegios administrativos
RUN groupadd --gid 10001 bladys && \
    useradd --uid 10001 \
            --gid 10001 \
            --create-home \
            --shell /usr/sbin/nologin \
            bladys

ENV PATH="/app/.venv/bin:$PATH"

ENV PYTHONDONTWRITEBYTECODE=1

USER bladys

EXPOSE 8000

CMD ["uvicorn", "bladys.main:app", "--host", "0.0.0.0", "--port", "8000"]
