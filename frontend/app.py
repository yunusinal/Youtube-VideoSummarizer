import streamlit as st
import requests
import json
import time
import re
from datetime import datetime
import isodate
import base64


# Sayfa yapılandırması
st.set_page_config(
    page_title="YouTube Video Özetleyici",
    page_icon="🎥",
    layout="wide",
)

# CSS stilleri
st.markdown(
    """
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
    
    /* Ana Başlıklar Stili */
    .bullet-points-container {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 10px;
        padding: 5px 0;
        margin: 10px 0;
    }
    .bullet-points-container ul {
        list-style-type: none;
        padding-left: 0;
        margin: 0;
    }
    .bullet-points-container li {
        padding: 10px 14px;
        margin: 6px 0;
        background: rgba(255, 255, 255, 0.95);
        border-radius: 12px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
        line-height: 1.5;
        font-size: 15px;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
        border-left: 5px solid #667eea;
    }
    .bullet-points-container li:hover {
        transform: translateX(5px);
        box-shadow: 0 6px 20px rgba(0,0,0,0.15);
    }
    .bullet-points-container li {
        display: flex;
        align-items: flex-start;
        gap: 12px;
    }
    .bullet-number {
        background: linear-gradient(135deg, #b3c6f7 0%, #c4b5fd 100%);
        color: #3b3b6b;
        padding: 4px 10px;
        border-radius: 8px;
        font-size: 12px;
        font-weight: 700;
        min-width: 32px;
        text-align: center;
        flex-shrink: 0;
    }
    .bullet-text {
        flex: 1;
    }
    
    /* Detaylı Özet Stili */
    
    .detailed-summary-container {
        background: transparent;
        border-radius: 0;
        padding: 0;
        margin: 0;
    }
    .madde-card {
        background: white;
        border-radius: 0;
        padding: 16px;
        margin: 0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        border: 1px solid #e2e8f0;
        border-bottom: none;
        transition: box-shadow 0.3s ease;
    }
    .madde-card:last-child {
        border-bottom: 1px solid #e2e8f0;
    }
    .madde-card:hover {
        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    .madde-header {
        background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%);
        color: white;
        padding: 5px 1px;
        border-radius: 0;
        margin-bottom: 1px;
        font-weight: 600;
        font-size: 17px;
        display: inline-flex;
        align-items: center;
        gap: 10px;
        width: fit-content;
    }
    .madde-number {
        background: rgba(255,255,255,0.2);
        padding: 6px 14px;
        border-radius: 20px;
        font-size: 14px;
        font-weight: 700;
    }
    .madde-content {
        color: #334155;
        line-height: 1.6;
        font-size: 15px;
        padding: 0;
    }
    .madde-content p {
        margin-bottom: 8px;
    }
    .quote-block {
        background: transparent;
        border-left: none;
        padding: 4px 0;
        margin: 8px 0;
        font-style: italic;
        color: #92400e;
    }
    .quote-block::before {
        content: '"';
        font-size: 20px;
        font-weight: bold;
        margin-right: 3px;
    }
    .timestamp {
        background: #dbeafe;
        color: #1e40af;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 13px;
        font-weight: 600;
        font-family: monospace;
        margin-left: 8px;
    }
    .context-section {
        background: linear-gradient(135deg, #ecfdf5 0%, #d1fae5 100%);
        border-radius: 0;
        padding: 12px 16px;
        margin-top: 12px;
        border-left: 4px solid #10b981;
    }
    .context-title {
        color: #065f46;
        font-weight: 700;
        font-size: 14px;
        margin-bottom: 6px;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .context-content {
        color: #047857;
        line-height: 1.6;
        font-size: 14px;
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
""",
    unsafe_allow_html=True,
)


def format_detailed_summary(summary_text):
    """Detaylı özeti güzel HTML formatına dönüştürür"""
    html_output = '<div class="detailed-summary-container">'

    # Maddeleri ayır (Madde # ile başlayan bölümler)
    sections = re.split(r"(?=\*\*Madde #|\*\*Madde\s+#)", summary_text)

    for section in sections:
        if not section.strip():
            continue

        # Madde başlığını ve içeriğini ayır
        lines = section.strip().split("\n")

        # Madde başlığını bul
        header_match = re.match(
            r"\*\*Madde\s*#?(\d+):?\s*(.+?)\*\*", lines[0] if lines else ""
        )

        if header_match:
            madde_num = header_match.group(1)
            madde_title = header_match.group(2).strip()

            html_output += f"""
            <div class="madde-card">
                <div class="madde-header">
                    <span class="madde-number">#{madde_num}</span>
                    <span>{madde_title}</span>
                </div>
                <div class="madde-content">
            """

            # İçeriği işle
            content_lines = lines[1:]
            content_text = "\n".join(content_lines)

            # Bağlam ve Bağlantılar bölümünü ayır
            context_match = re.split(r"\*\*Bağlam ve Bağlantılar:?\*\*", content_text)

            main_content = context_match[0] if context_match else content_text
            context_content = context_match[1] if len(context_match) > 1 else ""

            # Alıntıları işle - "..." (timestamp) formatı
            def format_quote(match):
                quote_text = match.group(1)
                timestamp = match.group(2) if match.lastindex >= 2 else ""
                timestamp_html = (
                    f'<span class="timestamp">{timestamp}</span>' if timestamp else ""
                )
                return f'<div class="quote-block">{quote_text}{timestamp_html}</div>'

            # Alıntı formatları: "[alıntı]" (00:00-00:00) veya "alıntı" (00:00-00:00)
            main_content = re.sub(
                r'["\"]([^"\"]+)["\"][\s]*\((\d{1,2}:\d{2}(?:-\d{1,2}:\d{2})?)\)',
                format_quote,
                main_content,
            )

            # **kalın** metinleri <strong> ile değiştir
            main_content = re.sub(
                r"\*\*(.+?)\*\*", r"<strong>\1</strong>", main_content
            )
            # "Bu madde videoda neden önemli?" kalıbını bold yap (model ** koymasa bile)
            main_content = re.sub(
                r"(Bu madde videoda neden önemli\?[^:]*:)",
                r"<strong>\1</strong>",
                main_content,
            )

            # Paragrafları düzenle
            paragraphs = main_content.strip().split("\n\n")
            for p in paragraphs:
                if p.strip():
                    html_output += f"<p>{p.strip()}</p>"

            # Bağlam bölümü
            if context_content.strip():
                context_content = re.sub(
                    r"\*\*(.+?)\*\*", r"<strong>\1</strong>", context_content
                )
                html_output += f"""
                <div class="context-section">
                    <div class="context-title">🔗 Bağlam ve Bağlantılar</div>
                    <div class="context-content">{context_content.strip()}</div>
                </div>
                """

            html_output += "</div></div>"
        else:
            # Başlık bulunamadıysa düz metin olarak ekle
            if section.strip():
                html_output += f'<div class="madde-card"><div class="madde-content"><p>{section.strip()}</p></div></div>'

    html_output += "</div>"
    return html_output


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


@st.cache_data(show_spinner=False)
def fetch_video_details(url: str):
    """Video detaylarını cache'ler — rerun'larda tekrar istek atmaz."""
    response = requests.post(VIDEO_DETAILS_URL, json={"url": url})
    if response.status_code == 200:
        return response.json()
    return None


@st.cache_data(show_spinner=False)
def fetch_thumbnail_bytes(thumbnail_url: str) -> bytes:
    """Thumbnail'i cache'ler — rerun'larda tekrar indirilmez."""
    return requests.get(thumbnail_url).content


# Session state başlatma
if "summaries" not in st.session_state:
    st.session_state.summaries = {}
# Toggle bar ile ilgili kodlar kaldırıldı
# URL giriş alanı
video_url = st.text_input(
    "YouTube Video URL'sini yapıştırın:",
    placeholder="https://www.youtube.com/watch?v=...",
)

if video_url:
    try:
        # Video detaylarını cache'den al (rerun'larda network isteği atmaz)
        with st.spinner("Video detayları yükleniyor..."):
            video_details = fetch_video_details(video_url)

        if video_details is None:
            st.error(
                "Video detayları alınamadı. Lütfen geçerli bir YouTube URL'si girin."
            )
            st.stop()

        # Video kartı
        st.markdown('<div class="video-card">', unsafe_allow_html=True)
        # Video başlığı
        st.markdown(f"### {video_details['title']}")

        # Video bilgileri
        col1, col2 = st.columns([1, 2])

        with col1:
            # Thumbnail cache'den gelir, her rerun'da yeniden indirilmez
            thumb_bytes = fetch_thumbnail_bytes(video_details["thumbnail"])
            st.image(thumb_bytes, use_column_width=True)

        with col2:
            # Video istatistikleri
            st.markdown('<div class="video-stats">', unsafe_allow_html=True)

            # Görüntülenme sayısı
            st.markdown(
                f"""
            <div class="stat-item">
                👁️ {int(video_details["view_count"]):,} görüntülenme
            </div>
            """,
                unsafe_allow_html=True,
            )

            # Beğeni sayısı
            st.markdown(
                f"""
            <div class="stat-item">
                👍 {int(video_details["like_count"]):,} beğeni
            </div>
            """,
                unsafe_allow_html=True,
            )

            # Video süresi
            duration = isodate.parse_duration(video_details["duration"])
            hours = duration.seconds // 3600
            minutes = (duration.seconds % 3600) // 60
            seconds = duration.seconds % 60
            duration_str = (
                f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                if hours > 0
                else f"{minutes:02d}:{seconds:02d}"
            )

            st.markdown(
                f"""
            <div class="stat-item">
                ⏱️ {duration_str}
            </div>
            """,
                unsafe_allow_html=True,
            )

            st.markdown("</div>", unsafe_allow_html=True)

            # Kanal bilgisi
            st.markdown(f"**Kanal:** {video_details['channel_title']}")

            # Yayın tarihi
            published_date = datetime.fromisoformat(
                video_details["published_at"].replace("Z", "+00:00")
            )
            st.markdown(f"**Yayınlanma Tarihi:** {published_date.strftime('%d/%m/%Y')}")

        st.markdown("</div>", unsafe_allow_html=True)

        # Özetleme butonu
        if st.button("Video'yu Özetle"):
            with st.spinner("Video özetleniyor..."):
                # API'ye istek gönderme
                response = requests.post(API_URL, json={"url": video_url})

                if response.status_code == 200:
                    result = response.json()
                    task_id = result["task_id"]

                    # Ana başlıkları hemen göster
                    st.subheader("📋 Ana Başlıklar")

                    # Bullet points'ı düzgün formata çevir
                    bullet_text = result["bullet_points"]
                    # • veya - ile başlayan maddeleri ayır ve liste haline getir
                    bullets = re.split(r"(?=•)|(?=-\s)", bullet_text)
                    bullets = [
                        b.strip().lstrip("•-").strip()
                        for b in bullets
                        if b.strip() and len(b.strip()) > 2
                    ]

                    # HTML liste olarak göster
                    if bullets:
                        bullet_html = '<div class="bullet-points-container"><ul>'
                        for i, bullet in enumerate(bullets, 1):
                            bullet_html += f'<li><span class="bullet-number">#{i}</span><span class="bullet-text">{bullet}</span></li>'
                        bullet_html += "</ul></div>"
                        st.markdown(bullet_html, unsafe_allow_html=True)
                    else:
                        st.markdown(bullet_text)

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
                        # Detaylı özeti güzel formatla göster
                        formatted_summary = format_detailed_summary(
                            status_data["result"]
                        )
                        detailed_summary_placeholder.markdown(
                            formatted_summary, unsafe_allow_html=True
                        )
                        # İndirme butonu ekle
                        try:
                            download_response = requests.get(
                                f"{DOWNLOAD_URL}/{task_id}"
                            )
                            if download_response.status_code == 200:
                                # İndirme butonu
                                st.download_button(
                                    label="📥 Özeti İndir",
                                    data=download_response.content,
                                    file_name=f"video_summary_{task_id}.txt",
                                    mime="text/plain",
                                    key=f"download_{task_id}",
                                )
                            else:
                                st.warning(
                                    "Özet indirilemedi. Lütfen daha sonra tekrar deneyin."
                                )
                        except Exception as e:
                            st.error(f"İndirme hatası: {str(e)}")

                        break
                        time.sleep(5)  # 5 saniye bekle

                else:
                    error_message = response.json().get(
                        "detail", "Bilinmeyen bir hata oluştu"
                    )
                    st.error(f"Hata oluştu: {error_message}")

    except requests.exceptions.ConnectionError:
        st.error(
            "Backend servisine bağlanılamıyor. Lütfen backend servisinin çalıştığından emin olun."
        )
    except Exception as e:
        st.error(f"Bir hata oluştu: {str(e)}")
else:
    st.warning("Lütfen bir YouTube video URL'si girin.")
