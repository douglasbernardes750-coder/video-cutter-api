FROM python:3.11-slim

# Instala ffmpeg e dependências
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    unzip \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Instala deno (runtime JS padrão do yt-dlp)
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

# Instala yt-dlp
RUN curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
    -o /usr/local/bin/yt-dlp && chmod +x /usr/local/bin/yt-dlp

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
