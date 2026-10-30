# Customer data platform (WishUnify) - app + pipeline image
FROM python:3.13-slim

# ODBC driver manager + Microsoft SQL Server driver for database sources
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg unixodbc \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
    > /etc/apt/sources.list.d/mssql.list \
 && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
 && apt-get purge -y curl gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 app
WORKDIR /opt/cdp

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cdp ./cdp
COPY sql ./sql
COPY app ./app
COPY config ./config

# Warehouse, source files and logs live on a volume.
ENV CDP_ENV=production \
    CDP_SECRETS_PATH=/data/secrets.enc \
    CDP_WAREHOUSE=/data/warehouse.duckdb \
    CDP_SOURCES_ROOT=/data/sources \
    CDP_LOG_DIR=/data/logs \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data && chown -R app:app /data /opt/cdp
USER app
VOLUME ["/data"]
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["streamlit", "run", "app/streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
