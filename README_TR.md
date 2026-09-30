# Ayşe Tesettür — Trendyol AI Uploader

Bu ilk çalışan iskelettir. Amaç: fotoğraf + fiyat + stok + beden + renk + kumaş + Model Kodu girerek Trendyol Product V2 payload'u oluşturmak ve onaydan sonra göndermektir.

## 1) Kurulum

Python 3.11+ önerilir.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env
```

`.env` içine Trendyol'dan YENİ API Key/Secret girin. Önceki konuşmada paylaşılmış anahtarları kullanmayın; yenileyin.

## 2) Çalıştırma

```bash
uvicorn app:app --reload
```

Tarayıcı:
http://127.0.0.1:8000

## 3) Önemli

Trendyol Product V2, brandId, categoryId ve category attribute/value bilgilerini resmi servislerden almayı gerektirir. Bu proje bunları tahmin etmez.

Geliştirme sırası:
1. Brand ID otomatik bulma
2. Category ID otomatik bulma
3. Category attributes/value IDs otomatik çekme
4. AI ile ürün adı/açıklama/attribute önerileri
5. İnsan onayı
6. Product V2 gönderimi
7. batchRequestId sonucu otomatik kontrol
8. fiyat/stok güncelleme
9. resim hosting (HTTPS) ve çoklu görsel
10. ürün logları ve hata ekranı

## Barkod kuralı

Model Kodu `narin15800` için:
narin15800-42
narin15800-44
narin15800-46
narin15800-48
narin15800-50
narin15800-52

Not: Trendyol barkod kuralları ve mağaza içi barkod politikası ayrıca doğrulanmalıdır.

## Görseller

Trendyol'a gönderilen image URL'lerinin Trendyol tarafından erişilebilir olması gerekir. Yerel `127.0.0.1` URL'leri Trendyol için kullanılamaz. Production sürümünde S3/Cloudinary/benzeri HTTPS storage eklenmelidir.

## Güvenlik

`.env` dosyasını paylaşmayın. API Secret/Token'ı koda yazmayın.
