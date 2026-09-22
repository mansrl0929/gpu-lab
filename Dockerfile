FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home portal
COPY portal ./portal
COPY config ./config
RUN mkdir /app/data && chown portal:portal /app/data
USER portal
ENV PORTAL_DB=/app/data/portal.sqlite3
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3)"
CMD ["uvicorn", "portal.app:app", "--host", "0.0.0.0", "--port", "8000"]
