import streamlit as st
import requests
import json
import time
from datetime import datetime
import isodate
import base64


# Sayfa yapılandırması
st.set_page_config(
    page_title="YouTube Video Özetleyici",
    page_icon="🎥",
    layout="wide",
    initial_sidebar_state="collapsed"  # Sidebar'ı başlangıçta kapalı olarak ayarla
)

# CSS stilleri
st.markdown("""
<style>
    .video-card {
        background-color: #f0f2f6;
        border-radius: 10px;
        padding: 20px;
        margin-bottom: 20px;
    }
    .video-info {
        display: flex;
        gap: 20px;
        align-items: start;
    }
    .video-stats {
        display: flex;
        gap: 15px;
        margin-top: 10px;
    }
    .stat-item {
        display: flex;
        align-items: center;
        gap: 5px;
    }
    .video-thumbnail {
        border-radius: 10px;
        max-width: 320px;
    }
    .processing {
        color: #1E88E5;
        padding: 10px;
        border-radius: 5px;
        margin: 10px 0;
    }
    .completed {
        color: #43A047;
        padding: 10px;
        border-radius: 5px;
        margin: 10px 0;
    }
    .error {
        color: #E53935;
        padding: 10px;
        border-radius: 5px;
        margin: 10px 0;
    }
    .download-button {
        background-color: #4CAF50;
        color: white;
        padding: 10px 20px;
        border: none;
        border-radius: 4px;
        cursor: pointer;
        font-size: 16px;
        margin-top: 10px;
    }
    .download-button:hover {
        background-color: #45a049;
    }
    .sidebar-summary {
        background-color: #f0f2f6;
        border-radius: 5px;
        padding: 10px;
        margin-bottom: 10px;
    }
    .sidebar-summary-title {
        font-weight: bold;
        margin-bottom: 5px;
    }
    .sidebar-summary-content {
        font-size: 0.9em;
    }
</style>
""", unsafe_allow_html=True)

# Başlık ve açıklama
st.title("🎥 YouTube Video Özetleyici")
st.markdown("""
Bu uygulama, YouTube videolarınızı otomatik olarak özetler. 
Video linkini yapıştırın ve özeti alın!
""")

# API endpoint'leri
API_URL = "http://localhost:8000/summarize"
VIDEO_DETAILS_URL = "http://localhost:8000/video-details"
STATUS_URL = "http://localhost:8000/summary-status"
DOWNLOAD_URL = "http://localhost:8000/download-summary"

# Session state başlatma
if 'summaries' not in st.session_state:
    st.session_state.summaries = {}
# Toggle bar ile ilgili kodlar kaldırıldı
# URL giriş alanı
video_url = st.text_input("YouTube Video URL'sini yapıştırın:", placeholder="https://www.youtube.com/watch?v=...")

if video_url:
    try:
        # Video detaylarını session_state'den al veya yeni detayları çek
        if 'video_details' not in st.session_state or st.session_state.current_url != video_url:
            with st.spinner('Video detayları yükleniyor...'):
                details_response = requests.post(VIDEO_DETAILS_URL, json={"url": video_url})
                
                if details_response.status_code == 200:
                    st.session_state.video_details = details_response.json()
                    st.session_state.current_url = video_url
                else:
                    st.error("Video detayları alınamadı. Lütfen geçerli bir YouTube URL'si girin.")
                    st.stop()
        
        # Video detaylarını göster
        video_details = st.session_state.video_details
        
        # Video kartı
        st.markdown('<div class="video-card">', unsafe_allow_html=True)
        # Video başlığı
        st.markdown(f"### {video_details['title']}")
        
        # Video bilgileri
        col1, col2 = st.columns([1, 2])
        
        with col1:
            st.image(video_details['thumbnail'], use_column_width=True)
        
        with col2:
            # Video istatistikleri
            st.markdown('<div class="video-stats">', unsafe_allow_html=True)
            
            # Görüntülenme sayısı
            st.markdown(f"""
            <div class="stat-item">
                👁️ {int(video_details['view_count']):,} görüntülenme
            </div>
            """, unsafe_allow_html=True)
            
            # Beğeni sayısı
            st.markdown(f"""
            <div class="stat-item">
                👍 {int(video_details['like_count']):,} beğeni
            </div>
            """, unsafe_allow_html=True)
            
            # Video süresi
            duration = isodate.parse_duration(video_details['duration'])
            hours = duration.seconds // 3600
            minutes = (duration.seconds % 3600) // 60
            seconds = duration.seconds % 60
            duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes:02d}:{seconds:02d}"
            
            st.markdown(f"""
            <div class="stat-item">
                ⏱️ {duration_str}
            </div>
            """, unsafe_allow_html=True)
            
            st.markdown('</div>', unsafe_allow_html=True)
            
            # Kanal bilgisi
            st.markdown(f"**Kanal:** {video_details['channel_title']}")
            
            # Yayın tarihi
            published_date = datetime.fromisoformat(video_details['published_at'].replace('Z', '+00:00'))
            st.markdown(f"**Yayınlanma Tarihi:** {published_date.strftime('%d/%m/%Y')}")
        
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Özetleme butonu
        if st.button("Video'yu Özetle"):
            with st.spinner('Video özetleniyor...'):
                # API'ye istek gönderme
                response = requests.post(API_URL, json={"url": video_url})
                
                if response.status_code == 200:
                    result = response.json()
                    task_id = result["task_id"]
                    
                    # Ana başlıkları hemen göster
                    st.subheader("📋 Ana Başlıklar")
                    st.markdown(result["bullet_points"])
                    
                    # Detaylı özet için ayrı bir bölüm
                    st.subheader("📝 Detaylı Özet")
                    status_placeholder = st.empty()
                    detailed_summary_placeholder = st.empty()
                    
                    # Durumu kontrol et
                    max_retries = 60  # 5 dakika (5 saniye aralıklarla)
                    for _ in range(max_retries):
                        status_response = requests.get(f"{STATUS_URL}/{task_id}")
                    
                        if status_response.status_code == 200:
                            status_data = status_response.json()
                                # Özeti session state'e kaydet
                        st.session_state.summaries[task_id] = {
                            'bullet_points': result["bullet_points"],
                            'detailed_summary': status_data["result"],
                            'video_details': video_details
                        }
                        # Detaylı özeti göster
                        detailed_summary_placeholder.markdown(status_data["result"])
                        # İndirme butonu ekle
                        try:
                            download_response = requests.get(f"{DOWNLOAD_URL}/{task_id}")
                            if download_response.status_code == 200:
                                # İndirme butonu
                                st.download_button(
                                    label="📥 Özeti İndir",
                                    data=download_response.content,
                                    file_name=f"video_summary_{task_id}.txt",
                                    mime="text/plain",
                                    key=f"download_{task_id}"
                                )
                            else:
                                st.warning("Özet indirilemedi. Lütfen daha sonra tekrar deneyin.")
                        except Exception as e:
                            st.error(f"İndirme hatası: {str(e)}")
                        
                        break
                        time.sleep(5)  # 5 saniye bekle
                    
                else:
                    error_message = response.json().get("detail", "Bilinmeyen bir hata oluştu")
                    st.error(f"Hata oluştu: {error_message}")
                    
    except requests.exceptions.ConnectionError:
        st.error("Backend servisine bağlanılamıyor. Lütfen backend servisinin çalıştığından emin olun.")
    except Exception as e:
        st.error(f"Bir hata oluştu: {str(e)}")
else:
    st.warning("Lütfen bir YouTube video URL'si girin.")

# Önceki özetleri sidebar'da göster
if st.session_state.summaries:
        for task_id, summary_data in st.session_state.summaries.items():
            with st.expander(f"🎥 {summary_data['video_details']['title'][:50]}..."):
                # Video detayları
                st.markdown(f"**Kanal:** {summary_data['video_details']['channel_title']}")
                
                # Ana başlıklar
                st.markdown("**📋 Ana Başlıklar:**")
                st.markdown(summary_data['bullet_points'])
                
                # Detaylı özet
                st.markdown("**📝 Detaylı Özet:**")
                st.markdown(summary_data['detailed_summary'])
                
                # İndirme butonu
                try:
                    download_response = requests.get(f"{DOWNLOAD_URL}/{task_id}")
                    if download_response.status_code == 200:
                        st.download_button(
                            label="📥 Bu Özeti İndir",
                            data=download_response.content,
                            file_name=f"video_summary_{task_id}.txt",
                            mime="text/plain",
                            key=f"download_sidebar_{task_id}"
                        )
                except Exception as e:
                    st.error(f"İndirme hatası: {str(e)}") 