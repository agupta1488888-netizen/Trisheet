# Runtime image for Railway. Config comes entirely from environment variables
# Railway injects; nothing here reads a .env file.
FROM python:3.11-slim

# WeasyPrint's native text-rendering libraries (see m12_assembler.assemble_pdf,
# which imports weasyprint lazily and degrades to an unavailable-artifact
# response if these are missing rather than crashing).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz-subset0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
