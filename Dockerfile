FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GEORENT_DATA_DIR=/app/data \
    GEORENT_EXPORT_DIR=/app/exports

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

# Użytkownik nieuprzywilejowany (uid 1000 odpowiada typowemu użytkownikowi hosta — wygodne dla wolumenów).
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data /app/exports \
    && chown -R app:app /app
USER app

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
