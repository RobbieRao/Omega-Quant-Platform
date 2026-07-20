FROM node:22-alpine AS web
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMEGA_DATABASE_URL=sqlite:////data/omega.sqlite
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY --from=web /build/frontend/dist ./frontend/dist
RUN pip install --no-cache-dir .
RUN useradd --create-home --uid 10001 omega && mkdir -p /data && chown -R omega:omega /app /data
USER omega
EXPOSE 8770
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8770/api/health', timeout=2)"
CMD ["omega-quant", "demo", "--host", "0.0.0.0", "--port", "8770"]
