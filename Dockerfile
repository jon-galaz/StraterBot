FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

# Install deps first — this layer is cached unless pyproject.toml changes
COPY pyproject.toml uv.lock* ./
COPY src/ src/
COPY main.py ./
RUN uv pip install --system --no-cache -e .

# Non-root user; create /app/data before chown so Docker initialises
# the named volume with correct ownership on first run.
RUN mkdir -p /app/data && \
    adduser --disabled-password --gecos "" appuser && \
    chown -R appuser /app
USER appuser

CMD ["python", "main.py"]
