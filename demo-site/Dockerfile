# Acme Records — the demo site the example graphs run against (stdlib Python only).
FROM python:3.13-alpine

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY server.py ./

EXPOSE 8090

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=2)"

CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8090"]
