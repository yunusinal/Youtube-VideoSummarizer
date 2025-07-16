from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import io


import google.generativeai as genai
from pydantic import BaseModel
import os
from youtube_transcript_api import YouTubeTranscriptApi
import re
from dotenv import load_dotenv
from googleapiclient.discovery import build

from prompts import BULLET_POINTS_PROMPT, DETAILED_SUMMARY_PROMPT


# Load environment variables
load_dotenv()
app = FastAPI()

# CORS ayarları (frontend ile backend arasında kullanılacak)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], #Hangi kaynaklardan gelen istekleri kabul edeceğini belirtir. * her isteği alır. Buraya sadece senin backendini yazabilirsin.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


GOOGLE_API_KEY =os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY environment variable is not set")

genai.configure(api_key=GOOGLE_API_KEY)

class VideoURL(BaseModel):
    url: str

# YouTube URL'sinden video ID'sini çıkaran fonksiyon
def extract_video_id(url: str) -> str:
    # regex deseni (?: ...) ile gruplandırma yapılıyor
    # ([^"&?\/\s]{11}) - 11 karakter uzunluğunda, boşluk, &, ?, / karakterleri olmayan bir grup yakalar
    pattern = r'(?:youtube\.com\/(?:watch\?v=|embed\/)|youtu\.be\/)([^"&?\/\s]{11})'
    
    match = re.search(pattern, url) #URL'de deseni ara 
    if match:
        return match.group(1) # Eğer eşleşme bulunduysa, video ID'sini döndür 0. grup kelimenin tamamını döndürür
    raise HTTPException(status_code=400, detail="Geçersiz YouTube URL'si. Video linkini kontrol ediniz. ")

def get_video_details(video_id: str) -> dict:
    # YouTube API kullanarak video detaylarını alır.
    try:
        youtube = build('youtube', 'v3', developerKey=os.getenv('YOUTUBE_API_KEY'))
        request = youtube.videos().list(
            part="snippet,contentDetails,statistics",
            id=video_id
        )
        # Video detaylarını al
        response = request.execute()
        if not response['items']:
            raise HTTPException(status_code=404, detail="Video bulunamadı")
            
        video = response['items'][0]
        # Video detaylarını döndür
        return {
            "title": video['snippet']['title'],
            "description": video['snippet']['description'],
            "thumbnail": video['snippet']['thumbnails']['high']['url'],
            "duration": video['contentDetails']['duration'],
            "language": video['snippet'].get('defaultAudioLanguage', 'Bilinmiyor'),
            "has_captions": video['contentDetails']['caption'] == 'true',
            "view_count": video['statistics'].get('viewCount', '0'),
            "like_count": video['statistics'].get('likeCount', '0'),
            "channel_title": video['snippet']['channelTitle'],
            "published_at": video['snippet']['publishedAt']
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Video detayları alınamadı: {str(e)}")

@app.post("/video-details")
async def get_video_details_endpoint(video: VideoURL):
    try:
        video_id = extract_video_id(video.url)
        return get_video_details(video_id)
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Beklenmeyen bir hata oluştu: {str(e)}")


def get_transcript(video_id: str) -> str:
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['tr', 'en'])
        return " ".join([item['text'] for item in transcript_list])
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Video transkripti alınamadı: {str(e)}")



# Özet durumlarını saklamak için global bir sözlük
summary_status = {}

@app.post("/summarize")
async def summarize_video(video: VideoURL, background_tasks: BackgroundTasks):
    try:
        video_id = extract_video_id(video.url)
        print(f"Video ID: {video_id}")
        transcript = get_transcript(video_id)
        model = genai.GenerativeModel('gemini-2.0-flash')
        
        # Ana başlıkları çıkarma
        BULLET_POINTS_PROMPT = BULLET_POINTS_PROMPT.format(transcript=transcript)
        
        bullet_points = model.generate_content(BULLET_POINTS_PROMPT)
        
        # Task ID oluştur
        import uuid
        task_id = str(uuid.uuid4())
        # Detaylı özeti arka planda işle
        background_tasks.add_task(process_detailed_summary, transcript, bullet_points.text, task_id)
        
        return {
            "bullet_points": bullet_points.text,
            "task_id": task_id,
            "message": "Ana başlıklar hazırlandı. Detaylı özet hazırlanıyor..."
        }
        
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Beklenmeyen bir hata oluştu: {str(e)}")


@app.get("/summary-status/{task_id}")
async def get_summary_status(task_id: str):
    """Özet işleminin durumunu kontrol et"""
    if task_id not in summary_status:
        raise HTTPException(status_code=404, detail="Task bulunamadı")
    return summary_status[task_id]



async def process_detailed_summary(transcript: str, bullet_points: str, task_id: str):
    try:
        print(f"Detaylı özet işleniyor... Task ID: {task_id}") #backend çalışan terminalde görünür.
        summary_status[task_id] = {"status": "processing", "result": None}
        
        model = genai.GenerativeModel('gemini-2.0-flash')
        
        # Detaylı özet (formatlamaya dikkat et)
        DETAILED_SUMMARY_PROMPT = DETAILED_SUMMARY_PROMPT.format(bullet_points=bullet_points, transcript=transcript)
        
        detailed_summary = model.generate_content(DETAILED_SUMMARY_PROMPT)
        
        # Sonucu sakla
        summary_status[task_id] = {
            "status": "completed",
            "result": detailed_summary.text
        }
        print(f"Detaylı özet hazırlandı. Task ID: {task_id}") #backendeki terminalde görünür. 
        
    except Exception as e:
        error_msg = f"Detaylı özet işlenirken hata oluştu: {str(e)}"
        print(error_msg)
        summary_status[task_id] = {
            "status": "error",
            "result": error_msg
        }



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
            }
        )
        
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Dosya indirme hatası: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 