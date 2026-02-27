from __future__ import annotations

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import time
import json
import uuid
import yt_dlp

import google.generativeai as genai
import requests as http_requests
from pydantic import BaseModel
import os
from youtube_transcript_api import YouTubeTranscriptApi
import re
from dotenv import load_dotenv
from googleapiclient.discovery import build

from prompts import BULLET_POINTS_PROMPT, DETAILED_SUMMARY_PROMPT

load_dotenv()


app = FastAPI()

# CORS ayarları (frontend ile backend arasında kullanılacak)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "*"
    ],  # Hangi kaynaklardan gelen istekleri kabul edeceğini belirtir. * her isteği alır. Buraya sadece senin backendini yazabilirsin.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY environment variable is not set")

genai.configure(api_key=GOOGLE_API_KEY)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


# Retry mekanizması ile Gemini API çağrısı
def generate_with_retry(model, prompt, max_retries=3, initial_delay=45):
    """Rate limit hatalarında otomatik retry yapan fonksiyon."""
    for attempt in range(max_retries):
        try:
            return model.generate_content(prompt)
        except Exception as e:
            error_str = str(e)
            if any(k in error_str.lower() for k in ["429", "quota", "rate"]):
                wait_time = initial_delay * (attempt + 1)
                print(
                    f"Rate limit aşıldı. {wait_time}s bekleniyor... ({attempt + 1}/{max_retries})"
                )
                time.sleep(wait_time)
            else:
                raise
    raise HTTPException(
        status_code=429,
        detail="API limiti aşıldı. Lütfen birkaç dakika sonra tekrar deneyin.",
    )


class VideoURL(BaseModel):
    url: str


def extract_video_id(url: str) -> str:
    """YouTube URL'sinden video ID'sini çıkaran fonksiyon."""
    # regex deseni (?: ...) ile gruplandırma yapılıyor
    # ([^"&?\/\s]{11}) - 11 karakter uzunluğunda, boşluk, &, ?, / karakterleri olmayan bir grup yakalar
    pattern = r'(?:youtube\.com\/(?:watch\?v=|embed\/)|youtu\.be\/)([^"&?\/\s]{11})'

    match = re.search(pattern, url)  # URL'de deseni ara
    if match:
        return match.group(
            1
        )  # Eğer eşleşme bulunduysa, video ID'sini döndür - 0. grup kelimenin tamamını döndürür
    raise HTTPException(
        status_code=400,
        detail="Geçersiz YouTube URL'si. Video linkini kontrol ediniz. ",
    )


def get_video_details(video_id: str) -> dict:
    """YouTube API kullanarak video detaylarını alır."""
    try:
        youtube = build("youtube", "v3", developerKey=os.getenv("YOUTUBE_API_KEY"))
        response = (
            youtube.videos()
            .list(part="snippet,contentDetails,statistics", id=video_id)
            .execute()
        )

        if not response["items"]:
            raise HTTPException(status_code=404, detail="Video bulunamadı")

        video = response["items"][0]
        return {
            "title": video["snippet"]["title"],
            "description": video["snippet"]["description"],
            "thumbnail": video["snippet"]["thumbnails"]["high"]["url"],
            "duration": video["contentDetails"]["duration"],
            "language": video["snippet"].get("defaultAudioLanguage", "Bilinmiyor"),
            "has_captions": video["contentDetails"]["caption"] == "true",
            "view_count": video["statistics"].get("viewCount", "0"),
            "like_count": video["statistics"].get("likeCount", "0"),
            "channel_title": video["snippet"]["channelTitle"],
            "published_at": video["snippet"]["publishedAt"],
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Video detayları alınamadı: {str(e)}"
        )


@app.post("/video-details")
async def get_video_details_endpoint(video: VideoURL):
    try:
        video_id = extract_video_id(video.url)
        return get_video_details(video_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Beklenmeyen bir hata oluştu: {str(e)}"
        )


def format_timestamp(seconds: float) -> str:
    """Saniyeyi mm:ss formatına çevirir"""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


def _fetch_transcript_with_api(api, video_id: str) -> str:
    """İlk yöntem: youtube_transcript_api ile transcript alır"""
    transcript_list = api.list(video_id)

    preferred_languages = ["tr", "en"]
    selected_transcript = None

    for lang in preferred_languages:
        for t in transcript_list:
            if t.language_code == lang:
                selected_transcript = t
                break
        if selected_transcript:
            break

    if not selected_transcript and transcript_list:
        selected_transcript = transcript_list[0]

    if not selected_transcript:
        raise Exception("Transkript bulunamadı.")

    transcript_data = selected_transcript.fetch()
    formatted_transcript = []
    for item in transcript_data:
        start_time = format_timestamp(item.start)
        end_time = format_timestamp(item.start + item.duration)
        formatted_transcript.append(f"[{start_time}-{end_time}] {item.text}")

    return "\n".join(formatted_transcript)


def _get_sub_url(sub_data: list) -> str | None:
    """Altyazı format listesinden json3 URL'sini (veya ilk mevcut URL'yi) döndürür."""
    for fmt in sub_data:
        if fmt.get("ext") == "json3":
            return fmt["url"]
    return sub_data[0].get("url") if sub_data else None


def _parse_json3_subtitle(url: str) -> list[str]:
    """JSON3 altyazı URL'sinden formatted transcript satırları döndürür."""
    resp = http_requests.get(url, timeout=30)
    resp.raise_for_status()
    events = resp.json().get("events", [])

    lines = []
    for event in events:
        segs = event.get("segs", [])
        if not segs:
            continue
        text = "".join(seg.get("utf8", "") for seg in segs).strip()
        if not text or text == "\n":
            continue
        start_sec = event.get("tStartMs", 0) / 1000
        end_sec = (event.get("tStartMs", 0) + event.get("dDurationMs", 0)) / 1000
        lines.append(
            f"[{format_timestamp(start_sec)}-{format_timestamp(end_sec)}] {text}"
        )
    return lines


def _find_subtitle_data(
    video_info: dict, lang: str | None = None
) -> tuple[list | None, str]:
    """video_info'dan belirtilen dilde (veya herhangi bir dilde) altyazı verisini bulur."""
    subs = video_info.get("subtitles", {})
    auto = video_info.get("automatic_captions", {})

    if lang:
        data = subs.get(lang) or auto.get(lang)
        return data, lang

    available = subs or auto
    if available:
        first_lang = next(iter(available))
        return available[first_lang], first_lang
    return None, ""


def _fetch_transcript_with_ytdlp(video_id: str) -> str:
    """
    Fallback yöntemi: yt-dlp Python modülü ile altyazı/caption indirir.
    youtube_transcript_api başarısız olduğunda devreye girer.
    """
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    # Önce tercih edilen dillerle, sonra dil belirtmeden dene
    langs_to_try = ["tr", "en", None]

    for lang in langs_to_try:
        try:
            ydl_opts = {
                "skip_download": True,
                "writeautomaticsub": True,
                "subtitlesformat": "json3",
                "quiet": True,
                "no_warnings": True,
            }
            if lang:
                ydl_opts["subtitleslangs"] = [lang]

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                video_info = ydl.extract_info(video_url, download=False)

            if not video_info:
                print(f"yt-dlp ({lang or 'any'}) video bilgisi alınamadı.")
                continue

            sub_data, found_lang = _find_subtitle_data(video_info, lang)
            if not sub_data:
                print(f"yt-dlp: {lang or 'any'} dilinde altyazı bulunamadı.")
                continue

            sub_url = _get_sub_url(sub_data)
            if not sub_url:
                continue

            lines = _parse_json3_subtitle(sub_url)
            if lines:
                print(f"yt-dlp ile transcript alındı (dil: {found_lang})")
                return "\n".join(lines)

        except Exception as e:
            print(f"yt-dlp ({lang or 'any'}) hatası: {str(e)[:300]}")

    raise Exception("yt-dlp ile de transcript alınamadı.")


def get_transcript(video_id: str) -> str:
    """
    Transcript alır:
    1) youtube_transcript_api ile dener (lokal)
    2) Başarısız olursa yt-dlp fallback kullanır (canlı için)
    """
    errors: list[str] = []

    # 1) youtube_transcript_api ile dene
    try:
        print("Yöntem 1: youtube_transcript_api ile deneniyor...")
        api = YouTubeTranscriptApi()
        result = _fetch_transcript_with_api(api, video_id)
        print("Transcript başarıyla alındı! (youtube_transcript_api)")
        return result
    except Exception as e:
        msg = f"youtube_transcript_api: {str(e)[:300]}"
        print(msg)
        errors.append(msg)

    # 2) yt-dlp fallback
    try:
        print("Yöntem 2: yt-dlp ile deneniyor...")
        result = _fetch_transcript_with_ytdlp(video_id)
        return result
    except Exception as e:
        msg = f"yt-dlp: {str(e)[:300]}"
        print(msg)
        errors.append(msg)

    # Her ikisi de başarısız
    detail = "Video transkripti alınamadı. " + " | ".join(errors)
    raise HTTPException(status_code=400, detail=detail)


@app.get("/debug-transcript/{video_id}")
async def debug_transcript(video_id: str):
    """Canlı ortamda transcript hatalarını teşhis etmek için debug endpoint."""
    import traceback as tb

    results: dict = {"video_id": video_id, "yt_dlp_version": yt_dlp.version.__version__}

    # youtube_transcript_api
    try:
        api = YouTubeTranscriptApi()
        transcript = _fetch_transcript_with_api(api, video_id)
        results["method1_youtube_transcript_api"] = {
            "status": "success",
            "lines": len(transcript.split("\n")),
            "preview": transcript[:200],
        }
    except Exception as e:
        results["method1_youtube_transcript_api"] = {
            "status": "error",
            "error": str(e)[:500],
            "traceback": tb.format_exc()[-500:],
        }

    # yt-dlp
    try:
        transcript = _fetch_transcript_with_ytdlp(video_id)
        results["method2_ytdlp"] = {
            "status": "success",
            "lines": len(transcript.split("\n")),
            "preview": transcript[:200],
        }
    except Exception as e:
        results["method2_ytdlp"] = {
            "status": "error",
            "error": str(e)[:500],
            "traceback": tb.format_exc()[-500:],
        }

    return results


summary_status = {}


@app.post("/summarize")
async def summarize_video(video: VideoURL, background_tasks: BackgroundTasks):
    try:
        video_id = extract_video_id(video.url)
        transcript = get_transcript(video_id)
        model = genai.GenerativeModel("gemini-2.0-flash")

        bullet_points = generate_with_retry(
            model, BULLET_POINTS_PROMPT.format(transcript=transcript)
        )

        task_id = str(uuid.uuid4())
        background_tasks.add_task(
            process_detailed_summary, transcript, bullet_points.text, task_id
        )

        return {
            "bullet_points": bullet_points.text,
            "task_id": task_id,
            "message": "Ana başlıklar hazırlandı. Detaylı özet hazırlanıyor...",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Beklenmeyen bir hata oluştu: {str(e)}"
        )


@app.get("/summary-status/{task_id}")
async def get_summary_status(task_id: str):
    """Özet işleminin durumunu kontrol et."""
    if task_id not in summary_status:
        raise HTTPException(status_code=404, detail="Task bulunamadı")
    return summary_status[task_id]


async def process_detailed_summary(transcript: str, bullet_points: str, task_id: str):
    try:
        print(f"Detaylı özet işleniyor... Task ID: {task_id}")
        summary_status[task_id] = {"status": "processing", "result": None}

        model = genai.GenerativeModel("gemini-2.0-flash")
        detailed_summary = generate_with_retry(
            model,
            DETAILED_SUMMARY_PROMPT.format(
                bullet_points=bullet_points, transcript=transcript
            ),
        )

        summary_status[task_id] = {
            "status": "completed",
            "result": detailed_summary.text,
        }
        print(f"Detaylı özet hazırlandı. Task ID: {task_id}")
    except Exception as e:
        summary_status[task_id] = {"status": "error", "result": str(e)}


@app.get("/download-summary/{task_id}")
async def download_summary(task_id: str):
    """Özeti text dosyası olarak indirir."""
    if task_id not in summary_status:
        raise HTTPException(status_code=404, detail="Özet bulunamadı")

    summary_data = summary_status[task_id]
    if summary_data["status"] != "completed":
        raise HTTPException(status_code=400, detail="Özet henüz hazır değil")

    return StreamingResponse(
        iter([summary_data["result"]]),
        media_type="text/plain",
        headers={
            "Content-Disposition": f"attachment; filename=video_summary_{task_id}.txt"
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
