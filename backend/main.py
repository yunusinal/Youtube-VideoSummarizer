from __future__ import annotations

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import time
import uuid
import base64
import tempfile
from collections import defaultdict
import yt_dlp

import google.generativeai as genai
import requests as http_requests
from pydantic import BaseModel
import os
from pathlib import Path
from youtube_transcript_api import YouTubeTranscriptApi
import re
from dotenv import load_dotenv
from googleapiclient.discovery import build

from prompts import BULLET_POINTS_PROMPT, DETAILED_SUMMARY_PROMPT

load_dotenv()


# ---- Cookie Desteği ----
# Aranacak cookie yolları (öncelik sırasıyla):
# 1. Render Secret Files: /etc/secrets/cookies.txt
# 2. Lokal: backend/cookies.txt
# 3. YT_COOKIES_BASE64 env'den oluşturulan dosya
_COOKIE_PATHS = [
    Path("/etc/secrets/cookies.txt"),  # Render Secret Files
    Path(__file__).parent / "cookies.txt",  # Lokal geliştirme
]


def _validate_cookie_line(line: str) -> bool:
    """
    Netscape cookie formatı: domain\tflag\tpath\tsecure\texpiration\tname\tvalue
    7 tab-separated alan gerekir, name boş olamaz.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return True  # Yorum veya boş satır — geçerli
    parts = stripped.split("\t")
    if len(parts) < 7:
        return False  # Eksik alan
    if not parts[5].strip():  # name alanı boş
        return False
    return True


def _sanitize_cookies(source: Path, dest: Path) -> None:
    """
    Cookie dosyasını okur, hatalı satırları çıkarır ve dest'e yazar.
    Boş value alanı olan cookie'ler hakkında uyarı verir.
    """
    raw = source.read_text(encoding="utf-8", errors="replace")
    good_lines = []
    empty_value_count = 0
    for line in raw.splitlines():
        if _validate_cookie_line(line):
            good_lines.append(line)
            # Boş value uyarısı
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                parts = stripped.split("\t")
                if len(parts) >= 7 and not parts[6].strip():
                    empty_value_count += 1
        else:
            print(f"Cookie: hatalı satır atlandı → {line!r}")
    dest.write_text("\n".join(good_lines) + "\n", encoding="utf-8")
    print(f"Cookie: {len(good_lines)} geçerli satır → {dest}")
    if empty_value_count > 0:
        print(
            f"⚠️  Cookie UYARI: {empty_value_count} cookie'nin value alanı boş! "
            "Cookies'i tarayıcıdan tekrar export edin."
        )


def _init_cookies() -> Path | None:
    """
    Cookie dosyasını bulur. Önce Render Secret Files, sonra lokal dosya,
    son çare olarak YT_COOKIES_BASE64 env'den oluşturur.
    Read-only dosya sistemi varsa /tmp'ye kopyalar ve satırları doğrular.
    """
    writable_path = Path(tempfile.gettempdir()) / "yt_cookies.txt"

    # Mevcut dosyaları kontrol et
    for path in _COOKIE_PATHS:
        if path.exists():
            print(f"cookies.txt bulundu: {path} ({path.stat().st_size} byte)")
            try:
                # Read-only olabilir, yazılabilir bir yere kopyala + temizle
                _sanitize_cookies(path, writable_path)
                return writable_path
            except Exception as e:
                print(f"Cookie kopyalama/temizleme hatası: {e}")
                return path  # En azından orijinali dene

    # Hiçbiri yoksa base64 env'den oluştur
    cookie_b64 = os.getenv("YT_COOKIES_BASE64")
    if cookie_b64:
        try:
            cookie_bytes = base64.b64decode(cookie_b64)
            writable_path.write_bytes(cookie_bytes)
            print(f"cookies.txt env'den oluşturuldu ({len(cookie_bytes)} byte)")
            # Oluşturulan dosyayı da doğrula
            _sanitize_cookies(writable_path, writable_path)
            return writable_path
        except Exception as e:
            print(f"Cookie decode hatası: {e}")

    print("UYARI: cookies.txt bulunamadı!")
    return None


COOKIES_FILE = _init_cookies()


def _get_cookies_path() -> str | None:
    """Eğer cookies.txt mevcutsa yolunu döndürür, yoksa None."""
    if COOKIES_FILE and COOKIES_FILE.exists():
        return str(COOKIES_FILE)
    return None


# ---- Rate Limiting ----
RATE_LIMIT_WINDOW = 60  # saniye
RATE_LIMIT_MAX = 5  # pencere başına maks istek (IP başına)
_rate_limit_store: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(client_ip: str):
    """IP başına dakikada max RATE_LIMIT_MAX istek izni verir."""
    now = time.time()
    _rate_limit_store[client_ip] = [
        t for t in _rate_limit_store[client_ip] if now - t < RATE_LIMIT_WINDOW
    ]
    if len(_rate_limit_store[client_ip]) >= RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Çok fazla istek. Lütfen {RATE_LIMIT_WINDOW} saniye sonra tekrar deneyin.",
        )
    _rate_limit_store[client_ip].append(now)


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
    errors: list[str] = []

    # Proxy desteği (env'den alınır, yoksa doğrudan bağlanır)
    proxy = os.getenv("YT_DLP_PROXY")

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
            if proxy:
                ydl_opts["proxy"] = proxy

            # Cookie desteği
            cookie_path = _get_cookies_path()
            if cookie_path:
                ydl_opts["cookiefile"] = cookie_path
                print("yt-dlp: cookies.txt kullanılıyor")

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                video_info = ydl.extract_info(video_url, download=False)

            if not video_info:
                msg = f"yt-dlp ({lang or 'any'}) video bilgisi alınamadı."
                print(msg)
                errors.append(msg)
                continue

            sub_data, found_lang = _find_subtitle_data(video_info, lang)
            if not sub_data:
                msg = f"yt-dlp: {lang or 'any'} dilinde altyazı bulunamadı."
                print(msg)
                errors.append(msg)
                continue

            sub_url = _get_sub_url(sub_data)
            if not sub_url:
                msg = f"yt-dlp ({lang or 'any'}) altyazı URL'si bulunamadı."
                errors.append(msg)
                continue

            lines = _parse_json3_subtitle(sub_url)
            if lines:
                print(f"yt-dlp ile transcript alındı (dil: {found_lang})")
                return "\n".join(lines)

        except Exception as e:
            msg = f"yt-dlp ({lang or 'any'}) hatası: {str(e)[:300]}"
            print(msg)
            errors.append(msg)

    raise Exception(
        "yt-dlp ile de transcript alınamadı. Detaylar: " + " | ".join(errors)
    )


def get_transcript(video_id: str) -> tuple[str, str]:
    """
    Transcript alır. Hangi yöntemle alındığını da döndürür.
    Returns: (transcript_text, method_name)
    1) youtube_transcript_api ile dener
    2) Başarısız olursa yt-dlp fallback kullanır
    """
    errors: list[str] = []

    # Proxy desteği
    proxy = os.getenv("YT_DLP_PROXY")

    # 1) youtube_transcript_api ile dene
    try:
        print("Yöntem 1: youtube_transcript_api ile deneniyor...")

        # Cookie desteği — http_client üzerinden Session'a cookie yüklenir
        cookie_path = _get_cookies_path()
        api_kwargs = {}

        if cookie_path:
            import requests as _req
            from http.cookiejar import MozillaCookieJar

            cj = MozillaCookieJar(cookie_path)
            try:
                cj.load(ignore_discard=True, ignore_expires=True)
                session = _req.Session()
                session.cookies = cj
                api_kwargs["http_client"] = session
                print(f"youtube_transcript_api: cookies yüklendi ({len(cj)} cookie)")
            except Exception as ce:
                print(f"youtube_transcript_api: cookie yükleme hatası: {ce}")

        if proxy:
            from youtube_transcript_api.proxies import GenericProxyConfig

            api_kwargs["proxy_config"] = GenericProxyConfig(
                http_url=proxy,
                https_url=proxy,
            )

        api = YouTubeTranscriptApi(**api_kwargs)
        result = _fetch_transcript_with_api(api, video_id)
        print("Transcript başarıyla alındı! (youtube_transcript_api)")
        return result, "youtube_transcript_api"
    except Exception as e:
        msg = f"youtube_transcript_api: {str(e)[:300]}"
        print(msg)
        errors.append(msg)

    # 2) yt-dlp fallback
    try:
        print("Yöntem 2: yt-dlp ile deneniyor...")
        result = _fetch_transcript_with_ytdlp(video_id)
        return result, "yt-dlp"
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

    proxy = os.getenv("YT_DLP_PROXY")
    cookie_path = _get_cookies_path()
    results: dict = {
        "video_id": video_id,
        "yt_dlp_version": yt_dlp.version.__version__,
        "proxy_configured": bool(proxy),
        "proxy_value": (proxy[:15] + "***") if proxy else None,
        "cookies_file_exists": cookie_path is not None,
    }

    # Proxy ile IP testi
    try:
        proxies = {"http": proxy, "https": proxy} if proxy else None
        ip_resp = http_requests.get(
            "https://api.ipify.org?format=json", proxies=proxies, timeout=10
        )
        results["outbound_ip"] = ip_resp.json().get("ip", "bilinmiyor")
    except Exception as e:
        results["outbound_ip_error"] = str(e)[:200]

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
async def summarize_video(
    video: VideoURL, request: Request, background_tasks: BackgroundTasks
):
    try:
        # Rate limit kontrolü
        client_ip = request.client.host if request.client else "unknown"
        _check_rate_limit(client_ip)

        video_id = extract_video_id(video.url)
        transcript, transcript_method = get_transcript(video_id)
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
            "transcript_method": transcript_method,
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
