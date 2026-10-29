# Optional container for VPS deployment. State lives in /app/var (mount a volume).
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY knowledge ./knowledge
RUN pip install --no-cache-dir . && useradd -m trader && mkdir -p var && chown trader var
USER trader
VOLUME ["/app/var"]
CMD ["tradeintel", "service", "--interval", "900"]
