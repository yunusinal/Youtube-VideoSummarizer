from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import io
import time
import random


import google.generativeai as genai
from pydantic import BaseModel
import os
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig
import re
from dotenv import load_dotenv
from googleapiclient.discovery import build

from prompts import BULLET_POINTS_PROMPT, DETAILED_SUMMARY_PROMPT


# Load environment variables (en başta yükle)
load_dotenv()

# Proxy olmadan varsayılan API instance
ytt_api = YouTubeTranscriptApi()

# Webshare.io ücretsiz proxy listesi
PROXY_LIST = [
    "31.59.20.176:6754",
    "23.95.150.145:6114",
    "198.23.239.134:6540",
    "45.38.107.97:6014",
    "107.172.163.27:6543",
    "198.105.121.200:6462",
    "64.137.96.74:6641",
    "216.10.27.159:6837",
    "142.111.67.146:5611",
    "23.26.53.37:6003",
]


def create_proxy_api(proxy_address: str = None):
    """Belirli veya rastgele bir proxy ile YouTubeTranscriptApi instance'ı oluşturur"""
    username = os.getenv("PROXY_USERNAME")
    password = os.getenv("PROXY_PASSWORD")

    if not username or not password:
        print("Proxy bilgileri bulunamadı, proxy'siz devam ediliyor...")
        return YouTubeTranscriptApi()

    proxy = proxy_address or random.choice(PROXY_LIST)
    proxy_url = f"http://{username}:{password}@{proxy}"
    print(f"Proxy kullanılıyor: {proxy}")

    proxy_config = GenericProxyConfig(
        http_url=proxy_url,
        https_url=proxy_url,
    )
    return YouTubeTranscriptApi(proxy_config=proxy_config)


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
    """
    Rate limit hatalarında otomatik retry yapan fonksiyon.
    Gemini ücretsiz tier limitlerini aşınca bekleyip tekrar dener.
    """
    for attempt in range(max_retries):
        try:
            return model.generate_content(prompt)
        except Exception as e:
            error_str = str(e)
            print(f"HATA DETAYI: {error_str}")  # Debug için
            # Rate limit hatası mı kontrol et
            if (
                "429" in error_str
                or "quota" in error_str.lower()
                or "rate" in error_str.lower()
            ):
                wait_time = initial_delay * (attempt + 1)  # Her seferinde artan bekleme
                print(
                    f"Rate limit aşıldı. {wait_time} saniye bekleniyor... (Deneme {attempt + 1}/{max_retries})"
                )
                time.sleep(wait_time)
            else:
                # Farklı bir hata ise direkt fırlat
                raise e

    # Tüm denemeler başarısız olduysa
    raise HTTPException(
        status_code=429,
        detail="API limiti aşıldı. Lütfen birkaç dakika sonra tekrar deneyin.",
    )


class VideoURL(BaseModel):
    url: str


# YouTube URL'sinden video ID'sini çıkaran fonksiyon
def extract_video_id(url: str) -> str:
    # regex deseni (?: ...) ile gruplandırma yapılıyor
    # ([^"&?\/\s]{11}) - 11 karakter uzunluğunda, boşluk, &, ?, / karakterleri olmayan bir grup yakalar
    pattern = r'(?:youtube\.com\/(?:watch\?v=|embed\/)|youtu\.be\/)([^"&?\/\s]{11})'

    match = re.search(pattern, url)  # URL'de deseni ara
    if match:
        return match.group(
            1
        )  # Eğer eşleşme bulunduysa, video ID'sini döndür 0. grup kelimenin tamamını döndürür
    raise HTTPException(
        status_code=400,
        detail="Geçersiz YouTube URL'si. Video linkini kontrol ediniz. ",
    )


def get_video_details(video_id: str) -> dict:
    # YouTube API kullanarak video detaylarını alır.
    try:
        youtube = build("youtube", "v3", developerKey=os.getenv("YOUTUBE_API_KEY"))
        request = youtube.videos().list(
            part="snippet,contentDetails,statistics", id=video_id
        )
        # Video detaylarını al
        response = request.execute()
        if not response["items"]:
            raise HTTPException(status_code=404, detail="Video bulunamadı")

        video = response["items"][0]
        # Video detaylarını döndür
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

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Video detayları alınamadı: {str(e)}"
        )


@app.post("/video-details")
async def get_video_details_endpoint(video: VideoURL):
    try:
        video_id = extract_video_id(video.url)
        return get_video_details(video_id)
    except HTTPException as he:
        raise he
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
    """Verilen API instance'ı ile transcript alır"""
    # Önce mevcut transkript dillerini kontrol et
    transcript_list = api.list(video_id)

    # Tercih sırası: Türkçe, İngilizce, sonra mevcut herhangi bir dil
    preferred_languages = ["tr", "en"]
    selected_transcript = None

    # Önce tercih edilen dilleri dene
    for lang in preferred_languages:
        for t in transcript_list:
            if t.language_code == lang:
                selected_transcript = t
                break
        if selected_transcript:
            break

    # Tercih edilen dil yoksa, mevcut ilk transkripti al
    if not selected_transcript and transcript_list:
        selected_transcript = transcript_list[0]

    if not selected_transcript:
        raise HTTPException(
            status_code=400, detail="Bu video için transkript bulunamadı."
        )

    # Transkripti al - timestamp'lerle birlikte formatla
    transcript_data = selected_transcript.fetch()
    formatted_transcript = []
    for item in transcript_data:
        start_time = format_timestamp(item.start)
        end_time = format_timestamp(item.start + item.duration)
        formatted_transcript.append(f"[{start_time}-{end_time}] {item.text}")

    return "\n".join(formatted_transcript)


def get_transcript(video_id: str) -> str:
    """
    Proxy ile transcript alır. Başarısız olursa farklı proxy'lerle tekrar dener.
    Son çare olarak proxy'siz direkt bağlantı dener.
    """
    last_error = None
    proxies_to_try = random.sample(PROXY_LIST, min(5, len(PROXY_LIST)))

    # 1) Proxy'lerle dene
    for attempt, proxy_addr in enumerate(proxies_to_try, 1):
        try:
            api = create_proxy_api(proxy_address=proxy_addr)
            print(
                f"Transcript alınıyor (proxy: {proxy_addr}) - Deneme {attempt}/{len(proxies_to_try)}"
            )
            result = _fetch_transcript_with_api(api, video_id)
            print(f"Transcript başarıyla alındı! (proxy: {proxy_addr})")
            return result

        except HTTPException as he:
            raise he
        except Exception as e:
            last_error = e
            print(f"Proxy denemesi {attempt} başarısız ({proxy_addr}): {str(e)}")
            continue

    # 2) Son çare: proxy'siz direkt bağlantı dene
    try:
        print("Tüm proxy'ler başarısız oldu. Direkt bağlantı deneniyor...")
        api = YouTubeTranscriptApi()
        result = _fetch_transcript_with_api(api, video_id)
        print("Transcript direkt bağlantı ile alındı!")
        return result
    except HTTPException as he:
        raise he
    except Exception as direct_error:
        print(f"Direkt bağlantı da başarısız: {str(direct_error)}")

    # Her şey başarısız olduysa
    raise HTTPException(
        status_code=400,
        detail=f"Video transkripti alınamadı. Proxy hatası: {str(last_error)}",
    )


# Özet durumlarını saklamak için global bir sözlük
summary_status = {}


@app.post("/summarize")
async def summarize_video(video: VideoURL, background_tasks: BackgroundTasks):
    try:
        video_id = extract_video_id(video.url)
        print(f"Video ID: {video_id}")

        # Transkripti al (yeni fonksiyonu kullan)
        transcript = get_transcript(video_id)
        model = genai.GenerativeModel("gemini-2.0-flash")

        # Ana başlıkları çıkarma
        formatted_bullet_prompt = BULLET_POINTS_PROMPT.format(transcript=transcript)

        # Retry mekanizması ile API çağrısı
        bullet_points = generate_with_retry(model, formatted_bullet_prompt)

        # Task ID oluştur
        import uuid

        task_id = str(uuid.uuid4())
        # Detaylı özeti arka planda işle
        background_tasks.add_task(
            process_detailed_summary, transcript, bullet_points.text, task_id
        )

        return {
            "bullet_points": bullet_points.text,
            "task_id": task_id,
            "message": "Ana başlıklar hazırlandı. Detaylı özet hazırlanıyor...",
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Beklenmeyen bir hata oluştu: {str(e)}"
        )


@app.get("/summary-status/{task_id}")
async def get_summary_status(task_id: str):
    """Özet işleminin durumunu kontrol et"""
    if task_id not in summary_status:
        raise HTTPException(status_code=404, detail="Task bulunamadı")
    return summary_status[task_id]


async def process_detailed_summary(transcript: str, bullet_points: str, task_id: str):
    try:
        print(
            f"Detaylı özet işleniyor... Task ID: {task_id}"
        )  # backend çalışan terminalde görünür.
        summary_status[task_id] = {"status": "processing", "result": None}

        model = genai.GenerativeModel("gemini-2.0-flash")

        # Detaylı özet (formatlamaya dikkat et)
        formatted_detailed_prompt = DETAILED_SUMMARY_PROMPT.format(
            bullet_points=bullet_points, transcript=transcript
        )

        # Retry mekanizması ile API çağrısı
        detailed_summary = generate_with_retry(model, formatted_detailed_prompt)

        # Sonucu sakla
        summary_status[task_id] = {
            "status": "completed",
            "result": detailed_summary.text,
        }
        print(
            f"Detaylı özet hazırlandı. Task ID: {task_id}"
        )  # backendeki terminalde görünür.

    except Exception as e:
        error_msg = f"Detaylı özet işlenirken hata oluştu: {str(e)}"
        print(error_msg)
        summary_status[task_id] = {"status": "error", "result": error_msg}


@app.get("/download-summary/{task_id}")
async def download_summary(task_id: str):
    """Özeti text dosyası olarak indirme"""
    try:
        if task_id not in summary_status:
            raise HTTPException(status_code=404, detail="Özet bulunamadı")

        summary_data = summary_status[task_id]
        if summary_data["status"] != "completed":
            raise HTTPException(status_code=400, detail="Özet henüz hazır değil")

        # Text dosyası oluştur
        text_content = summary_data["result"]

        # StringIO kullanarak bellek üzerinde dosya oluştur
        file_obj = io.StringIO(text_content)

        # StreamingResponse ile dosyayı indir
        return StreamingResponse(
            iter([file_obj.getvalue()]),
            media_type="text/plain",
            headers={
                "Content-Disposition": f"attachment; filename=video_summary_{task_id}.txt"
            },
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Dosya indirme hatası: {str(e)}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
