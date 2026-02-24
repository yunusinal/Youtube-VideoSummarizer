# YouTube Video Özetleyici

Bu proje, YouTube videolarının içeriğini otomatik olarak özetleyen iki parçalı bir uygulamadır: 
- **Backend:** FastAPI ile yazılıd ve Google Gemini ile özetleme yapar.
- **Frontend:** Streamlit ile kullanıcı dostu bir arayüz sunar.

## Özellikler
- YouTube video linki girerek ana başlıklar ve detaylı özet alabilirsiniz.
- Gemini API ile güçlü ve anlamlı özetleme.
- Türkçe ve İngilizce altyazı desteği.
- Özetleri .txt dosyası olarak indirebilme.

## Yakın bir zamanda gelecek özellikler

- **Geçmiş Özetler:**  
  Kullanıcılar, daha önce özetledikleri videoların özetlerini uygulama arayüzünde görebilecek ve bu özetlere tekrar erişebilecekler.
- Kullanıcılar bir websitesi üzerinden siteye erişebilecekler. Local olarak kurulum yapmak zorunda kalınmayacak.   
---

## Lokal Kurulum

### 1. Depoyu Klonlayın
```bash
git clone https://github.com/kullaniciadi/youtubeVideoSummarize.git
cd youtubeVideoSummarize
```
### 2. Ortamı Hazırlayın
Python 3.9+ önerilir.

```bash
python -m venv venv
# Windows: venv\Scripts\activate
# Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
```

### 3. Ortam Değişkenlerini Ayarlayın

Backend, Google Gemini API ve YouTube Data API keylerine ihtiyaç duyar. 
Proje dosyasının içinde bir `.env` dosyası oluşturun ve aşağıdaki gibi doldurun:

```
GOOGLE_API_KEY=your_gemini_api_key
YOUTUBE_API_KEY=your_youtube_data_api_key
```
- [Google Gemini API Key nasıl alınır?](https://aistudio.google.com/app/apikey)
- [YouTube Data API Key nasıl alınır?](https://console.developers.google.com/)

---

## Local olarak Kullanım

### 1. Backend'i Başlatın

```bash
cd backend
uvicorn main:app --reload
```
Varsayılan olarak `http://localhost:8000` adresinde çalışır.

### 2. Frontend'i Başlatın

Başka bir terminalde:

```bash
cd frontend
streamlit run app.py
```

Varsayılan olarak `http://localhost:8501` adresinde açılır.

---

## Dosya Yapısı

```
youtubeVideoSummarize/
├── backend/
│   ├── main.py              # FastAPI backend
│   ├── prompts.py           # Prompt metinleri
├── frontend/
│   └── app.py               # Streamlit arayüzü
├── requirements.txt         # Ortak gereksinimler
└── .env                     # Ortam değişkenleri (kendi bilgisayarınızda oluşturun)
```

---

## Notlar
- API anahtarlarınızın doğru olduğundan emin olun.
- Eğer özetleme çalışmazsa, terminaldeki hata mesajlarını kontrol edin.
- 
- Sadece eğitim ve kişisel kullanım içindir.

---


