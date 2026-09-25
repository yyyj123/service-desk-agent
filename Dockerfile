FROM python:3.10-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ANONYMIZED_TELEMETRY=False
WORKDIR /app
COPY requirements-enterprise.lock.txt .
RUN pip install --no-cache-dir -r requirements-enterprise.lock.txt && useradd --uid 10001 --create-home appuser
COPY service ./service
COPY portal ./portal
COPY knowledge ./knowledge
RUN mkdir -p /app/runtime && chown -R appuser:appuser /app
USER appuser
EXPOSE 8600
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8600/health/ready')"
CMD ["python", "-m", "service.manage", "serve", "--host", "0.0.0.0"]
