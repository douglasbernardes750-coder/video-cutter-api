import os
import re
import uuid
import base64
import subprocess
import httpx
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi

app = FastAPI()

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
N8N_APPROVAL_WEBHOOK = os.environ["N8N_APPROVAL_WEBHOOK"]
YOUTUBE_COOKIES_B64 = os.environ.get("YOUTUBE_COOKIES_B64", "")

WORK_DIR = Path("/tmp/clips")
WORK_DIR.mkdir(exist_ok=True)
COOKIES_PATH = Path("/tmp/yt_cookies.txt")


def write_cookies():
    if YOUTUBE_COOKIES_B64:
        COOKIES_PATH.write_bytes(base64.b64decode(YOUTUBE_COOKIES_B64))
    return COOKIES_PATH if COOKIES_PATH.exists() else None


def extract_video_id(url: str) -> str:
    m = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
    if not m:
        raise HTTPException(400, "URL do YouTube inválida")
    return m.group(1)


class ProcessRequest(BaseModel):
    youtube_url: str


class CutRequest(BaseModel):
    youtube_url: str
    start_time: float
    end_time: float
    clip_id: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/process")
async def process_video(req: ProcessRequest):
    clip_id = str(uuid.uuid4())
    video_id = extract_video_id(req.youtube_url)

    # Transcrição via API nativa do YouTube (sem download, sem bot detection)
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(
            video_id, languages=["pt", "pt-BR", "en", "en-US"]
        )
    except Exception as e:
        raise HTTPException(500, f"Transcrição falhou: {str(e)}")

    transcript_text = " ".join(t["text"] for t in transcript_list)
    segments_text = "\n".join(
        f"[{t['start']:.1f}s - {t['start'] + t['duration']:.1f}s]: {t['text']}"
        for t in transcript_list
    )

    # Análise dos melhores momentos via Groq Llama (grátis)
    async with httpx.AsyncClient(timeout=60) as client:
        analysis_resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Você é especialista em criação de conteúdo viral para YouTube Shorts. "
                            "Analise a transcrição e identifique os 3 melhores momentos para clips virais de 15 a 58 segundos. "
                            "Responda APENAS com JSON válido, sem markdown."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Transcrição com timestamps:\n{segments_text}\n\n"
                            'Retorne JSON: {"clips": [{"start_time": 0, "end_time": 30, "title": "...", "reason": "..."}]}'
                        ),
                    },
                ],
                "temperature": 0.7,
            },
        )

    if analysis_resp.status_code != 200:
        raise HTTPException(500, f"Análise falhou: {analysis_resp.text[:500]}")

    content = analysis_resp.json()["choices"][0]["message"]["content"].strip()

    # Remove markdown se o modelo colocar
    for marker in ["```json", "```"]:
        if marker in content:
            content = content.split(marker)[1].split("```")[0].strip()

    try:
        clips_data = json.loads(content)
    except Exception:
        raise HTTPException(500, f"Resposta inválida da IA: {content[:300]}")

    return {"clip_id": clip_id, "transcript": transcript_text, "clips": clips_data.get("clips", [])}


@app.post("/cut")
async def cut_video(req: CutRequest):
    output_path = WORK_DIR / f"{req.clip_id}.mp4"
    duration = req.end_time - req.start_time

    cookies_path = write_cookies()
    cmd = [
        "yt-dlp",
        "--download-sections", f"*{req.start_time}-{req.end_time}",
        "--force-keyframes-at-cuts",
        "--format", "18/best[height<=480][ext=mp4]/best[ext=mp4]",
        "-o", str(output_path),
    ]
    if cookies_path:
        cmd += ["--cookies", str(cookies_path)]
    cmd.append(req.youtube_url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0 or not output_path.exists():
        raise HTTPException(500, f"Download do trecho falhou: {result.stderr[:500]}")

    storage_path = f"clips/{req.clip_id}.mp4"

    async with httpx.AsyncClient(timeout=120) as client:
        with open(output_path, "rb") as f:
            upload_resp = await client.post(
                f"{SUPABASE_URL}/storage/v1/object/video-clips/{storage_path}",
                headers={"Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "video/mp4"},
                content=f,
            )

    output_path.unlink(missing_ok=True)

    if upload_resp.status_code not in (200, 201):
        raise HTTPException(500, f"Upload falhou: {upload_resp.text[:500]}")

    clip_url = f"{SUPABASE_URL}/storage/v1/object/public/video-clips/{storage_path}"
    return {"clip_id": req.clip_id, "clip_url": clip_url, "duration": duration}


@app.get("/dashboard/{clip_id}", response_class=HTMLResponse)
async def dashboard(clip_id: str, title: str = "", reason: str = ""):
    clip_url = f"{SUPABASE_URL}/storage/v1/object/public/video-clips/clips/{clip_id}.mp4"

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Glasdou — Aprovar Clip</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: sans-serif; background: #0f0f0f; color: #fff; min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 24px; }}
    h1 {{ font-size: 22px; color: #ff0000; margin-bottom: 20px; }}
    .card {{ background: #1a1a1a; border-radius: 12px; padding: 20px; max-width: 420px; width: 100%; }}
    video {{ width: 100%; border-radius: 8px; margin: 16px 0; }}
    .label {{ font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }}
    .value {{ font-size: 15px; margin-bottom: 16px; }}
    .buttons {{ display: flex; gap: 10px; }}
    button {{ flex: 1; padding: 14px; font-size: 15px; font-weight: bold; border: none; border-radius: 8px; cursor: pointer; }}
    .approve {{ background: #00c853; color: #fff; }}
    .reject {{ background: #d32f2f; color: #fff; }}
    .result {{ margin-top: 16px; font-size: 16px; text-align: center; min-height: 24px; }}
  </style>
</head>
<body>
  <h1>YouTube Shorts — Aprovação</h1>
  <div class="card">
    <div class="label">Título sugerido</div>
    <div class="value">{title}</div>
    <div class="label">Por que é viral</div>
    <div class="value">{reason}</div>
    <video controls playsinline src="{clip_url}"></video>
    <div class="buttons">
      <button class="approve" onclick="vote('approve')">Aprovar e Postar</button>
      <button class="reject" onclick="vote('reject')">Rejeitar</button>
    </div>
    <div class="result" id="result"></div>
  </div>
  <script>
    async function vote(decision) {{
      document.querySelector('.buttons').innerHTML = '<em>Processando...</em>';
      await fetch('{N8N_APPROVAL_WEBHOOK}', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{ clip_id: '{clip_id}', decision }})
      }});
      document.getElementById('result').textContent =
        decision === 'approve' ? 'Aprovado! Postando em breve.' : 'Rejeitado. Buscando próximo clip...';
    }}
  </script>
</body>
</html>"""
    return html
