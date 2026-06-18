FROM python:3.11-slim

# Instala ffmpeg e dependências do sistema
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Instala yt-dlp
RUN curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
    -o /usr/local/bin/yt-dlp && chmod +x /usr/local/bin/yt-dlp

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
