import os
import json
import requests
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Fallback to config.env if .env doesn't exist
if os.path.exists(os.path.join(BASE_DIR, ".env")):
    load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)
else:
    load_dotenv(os.path.join(BASE_DIR, "config.env"), override=True)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

db_client = None
def init_firebase():
    global db_client
    if db_client is not None:
        return db_client
    
    cred_env = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if cred_env:
        try:
            cred_dict = json.loads(cred_env)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            print(f"HATA: FIREBASE_SERVICE_ACCOUNT_JSON okunamadı: {e}")
            firebase_admin.initialize_app()
    else:
        local_key = os.path.join(BASE_DIR, "serviceAccountKey.json")
        if os.path.exists(local_key):
            cred = credentials.Certificate(local_key)
            firebase_admin.initialize_app(cred)
        else:
            firebase_admin.initialize_app()

    db_client = firestore.client()
    return db_client

db = init_firebase()

def get_api_keys():
    keys_str = os.getenv("GEMINI_API_KEYS")
    if keys_str:
        return [k.strip() for k in keys_str.split(",") if k.strip()]
        
    try:
        doc = db.collection("system_config").document("api_keys").get()
        if doc.exists:
            val = doc.to_dict().get("gemini_api_keys")
            if val:
                return [k.strip() for k in val.split(",") if k.strip()]
    except Exception as e:
        print("Error getting keys from Firestore:", e)
    return []

wiki_settings_cache = None

def get_wiki_settings():
    global wiki_settings_cache
    if wiki_settings_cache is not None:
        return wiki_settings_cache
        
    try:
        doc = db.collection("system_config").document("wiki_settings").get()
        if doc.exists:
            wiki_settings_cache = doc.to_dict()
            return wiki_settings_cache
    except Exception as e:
        print("Error getting wiki settings from Firestore:", e)
        
    return {
        "categories": [],
        "machine_map": {},
        "general_docs": []
    }

def call_gemini(prompt):
    api_keys = get_api_keys()
    keys_pool = list(api_keys)
    import random
    random.shuffle(keys_pool)
    
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return "⚠️ Hata: google-genai kütüphanesi yüklü değil."
        
    for key in keys_pool[:3]:
        try:
            client = genai.Client(api_key=key)
            model = "gemma-4-31b-it"
            contents = [
                types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
            ]
            
            # HIGH Thinking Mode Enabled - GitHub Actions has 6 hours!
            generate_content_config = types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(
                    thinking_level="HIGH",
                )
            )

            response_text = ""
            for chunk in client.models.generate_content_stream(
                model=model,
                contents=contents,
                config=generate_content_config,
            ):
                if chunk.text:
                    response_text += chunk.text
            
            if response_text:
                return response_text + "\n\n*(Model: gemma-4-31b-it - GitHub Actions)*"
        except Exception as e:
            print(f"GenAI SDK request failed for gemma: {e}", flush=True)
            
    for key in keys_pool[:3]:
        try:
            client = genai.Client(api_key=key)
            model = "gemini-2.5-flash"
            response = client.models.generate_content(
                model=model,
                contents=prompt
            )
            if response.text:
                return response.text + "\n\n*(Model: gemini-2.5-flash)*"
        except Exception as e:
            print(f"GenAI Fallback request failed: {e}")
            
    return "⚠️ Hata: Tüm API anahtarları denendi ancak yanıt alınamadı."

def send_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    
    # Telegram limit is 4096 chars. Split into chunks of 4000.
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    
    for idx, chunk in enumerate(chunks):
        payload = {
            "chat_id": str(chat_id),
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        # Attach markup only to the last chunk
        if reply_markup and idx == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
            
        try:
            response = requests.post(url, json=payload, timeout=10)
            if not response.ok:
                print(f"Telegram API Error: {response.text}", flush=True)
                # Eğer HTML tag hatası varsa (400), parse_mode olmadan tekrar dene
                if response.status_code == 400 and "parse entities" in response.text.lower():
                    print("Falling back to plain text mode...", flush=True)
                    payload.pop("parse_mode", None)
                    requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"Error sending message: {e}", flush=True)

def edit_message(chat_id, message_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"
    payload = {
        "chat_id": str(chat_id),
        "message_id": int(message_id),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error editing message: {e}")

def answer_callback(callback_query_id, text=None):
    url = f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error answering callback: {e}")

def get_user_state(chat_id):
    try:
        doc = db.collection("bot_states").document(str(chat_id)).get()
        if doc.exists:
            return doc.to_dict()
    except Exception as e:
        print(f"Error reading state: {e}")
    return {"state": "idle"}

def save_user_state(chat_id, state_info):
    try:
        db.collection("bot_states").document(str(chat_id)).set(state_info)
    except Exception as e:
        print(f"Error saving state: {e}")

def get_main_menu_markup():
    settings = get_wiki_settings()
    categories = settings.get("categories", [])
    
    buttons = []
    row = []
    for cat in categories:
        row.append({"text": cat.get("name"), "callback_data": f"cat:{cat.get('id')}"})
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
        
    return {"inline_keyboard": buttons}

def process_callback_query(callback_query):
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    data = callback_query["data"]
    cb_id = callback_query["id"]
    
    if data == "main_menu":
        answer_callback(cb_id)
        save_user_state(chat_id, {"state": "idle"})
        edit_message(
            chat_id, message_id,
            "🤖 <b>Vardiya Raporları & Arıza Wiki Sistemi</b>\n\nAşağıdaki kategorilerden tıklayarak makine seçebilir veya doğrudan bir arıza sorusu yazabilirsiniz:",
            get_main_menu_markup()
        )
        return
        
    if data.startswith("cat:"):
        category = data.split(":", 1)[1]
        answer_callback(cb_id)
        docs = db.collection("wiki_database").where("kategori", "==", category).stream()
        
        buttons = []
        row = []
        for doc in docs:
            doc_id = doc.id
            # Protect factory machine codes by stripping the prefix dynamically instead of hardcoding
            display_name = doc_id.split("_", 1)[-1] if "_" in doc_id else doc_id
            row.append({"text": display_name, "callback_data": f"doc:{doc_id}"})
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
            
        buttons.append([{"text": "🔙 Ana Menüye Dön", "callback_data": "main_menu"}])
        cat_title = category.replace("_", " ")
        edit_message(
            chat_id, message_id,
            f"📂 <b>{cat_title} Makineleri</b>\n\nDetaylı arıza kaydını görmek istediğiniz makineyi seçin:",
            {"inline_keyboard": buttons}
        )
        return
        
    if data.startswith("doc:"):
        doc_id = data.split(":", 1)[1]
        answer_callback(cb_id)
        
        doc_ref = db.collection("wiki_database").document(doc_id).get()
        if not doc_ref.exists:
            send_message(chat_id, "⚠️ Hata: Makine verisi bulunamadı.")
            return
            
        doc_data = doc_ref.to_dict()
        kategori = doc_data.get("kategori", "")
        olaylar = doc_data.get("olaylar", [])
        
        save_user_state(chat_id, {"state": "selected_doc", "doc_id": doc_id})
        
        text = (
            f"🛠️ <b>Makine: {doc_id}</b>\n"
            f"📂 <b>Kategori:</b> {kategori.replace('_', ' ')}\n"
            f"📊 <b>Kayıtlı Arıza Sayısı:</b> {len(olaylar)}\n\n"
            f"👇 Aşağıdaki menüden son arızaları listeleyebilir, arama başlatabilir veya <b>doğrudan bu makineye özel arıza sorusu yazıp gönderebilirsiniz!</b>"
        )
        
        markup = {
            "inline_keyboard": [
                [
                    {"text": "📝 Son 5 Arızayı Göster", "callback_data": f"show:{doc_id}:5"},
                    {"text": "🔍 Arıza Ara", "callback_data": f"search_init:{doc_id}"}
                ],
                [
                    {"text": "🔙 Makine Listesine Dön", "callback_data": f"cat:{kategori}"}
                ]
            ]
        }
        edit_message(chat_id, message_id, text, markup)
        return
        
    if data.startswith("show:"):
        parts = data.split(":")
        doc_id = parts[1]
        count = int(parts[2])
        answer_callback(cb_id)
        
        doc_ref = db.collection("wiki_database").document(doc_id).get()
        olaylar = doc_ref.to_dict().get("olaylar", [])
        
        text = f"📝 <b>{doc_id} - Son {count} Arıza Kaydı:</b>\n"
        text += "──────────────────────────────\n\n"
        
        last_olaylar = olaylar[-count:] if len(olaylar) >= count else olaylar
        if not last_olaylar:
            text += "<i>Henüz arıza kaydı girilmemiş.</i>"
        else:
            for idx, olay in enumerate(reversed(last_olaylar), start=1):
                text += f"<b>Olay {idx}:</b>\n"
                if olay.get("neden"):
                    text += f"• <b>Neden:</b> {olay['neden']}\n"
                if olay.get("cozum"):
                    text += f"• <b>Çözüm:</b> {olay['cozum']}\n"
                text += "\n"
                
        markup = {
            "inline_keyboard": [
                [{"text": "🔙 Geri Dön", "callback_data": f"doc:{doc_id}"}]
            ]
        }
        edit_message(chat_id, message_id, text, markup)
        return

    if data.startswith("search_init:"):
        doc_id = data.split(":", 1)[1]
        answer_callback(cb_id)
        
        save_user_state(chat_id, {"state": "waiting_for_search", "doc_id": doc_id})
        
        text = (
            f"🔍 <b>{doc_id} - Arıza Arama</b>\n\n"
            f"Lütfen aramak istediğiniz anahtar kelimeyi veya hatayı yazıp gönderin.\n"
            f"Örnek: <code>sleeve</code> veya <code>hata 2a2</code>"
        )
        
        markup = {
            "inline_keyboard": [
                [{"text": "🔙 Vazgeç", "callback_data": f"doc:{doc_id}"}]
            ]
        }
        edit_message(chat_id, message_id, text, markup)
        return

def handle_user_text(chat_id, text):
    state_info = get_user_state(chat_id)
    state = state_info.get("state")
    
    if text.startswith("/"):
        save_user_state(chat_id, {"state": "idle"})
        send_message(
            chat_id,
            "🤖 <b>Vardiya Raporları & Arıza Wiki Botuna Hoş Geldiniz!</b>\n\nAşağıdaki menüden kategorilere göre tıklayarak ilerleyebilirsiniz. Ya da doğrudan bir arıza sorusu yazıp gönderebilirsiniz:",
            get_main_menu_markup()
        )
        return
        
    db_ref = db.collection("wiki_database")
    
    if state == "waiting_for_search":
        doc_id = state_info.get("doc_id")
        save_user_state(chat_id, {"state": "selected_doc", "doc_id": doc_id})
        
        doc = db_ref.document(doc_id).get()
        olaylar = doc.to_dict().get("olaylar", [])
        
        matches = []
        for o in olaylar:
            if text.lower() in o.get("neden", "").lower() or text.lower() in o.get("cozum", "").lower():
                matches.append(o)
                
        response = f"🔍 <b>{doc_id} - Arama Sonuçları ({text}):</b>\n"
        response += "──────────────────────────────\n\n"
        if not matches:
            response += "<i>Eşleşen arıza kaydı bulunamadı.</i>"
        else:
            for idx, o in enumerate(matches[:5], start=1):
                response += f"<b>Olay {idx}:</b>\n• <b>Neden:</b> {o.get('neden')}\n• <b>Çözüm:</b> {o.get('cozum')}\n\n"
            if len(matches) > 5:
                response += f"<i>...ve {len(matches)-5} kayıt daha bulundu.</i>"
                
        markup = {
            "inline_keyboard": [
                [{"text": "🔙 Makine Sayfasına Dön", "callback_data": f"doc:{doc_id}"}]
            ]
        }
        send_message(chat_id, response, markup)
        
    else:
        doc_id = state_info.get("doc_id")
        
        if doc_id:
            doc = db_ref.document(doc_id).get()
            docs_data = [(doc_id, doc.to_dict())]
            scope_desc = f"Sadece <b>{doc_id}</b> makinesine ait"
        else:
            query_lower = text.lower()
            settings = get_wiki_settings()
            machine_map = settings.get("machine_map", {})
            general_docs = settings.get("general_docs", [])
            
            matched_ids = []
            for kw, doc_ids in machine_map.items():
                if kw in query_lower:
                    matched_ids.extend(doc_ids)
                    
            matched_ids = list(set(matched_ids))
            
            if not matched_ids:
                matched_ids = general_docs
                scope_desc = "Genel raporlar"
            else:
                scope_desc = f"Makineler ({', '.join(matched_ids)})"
                
            docs_data = []
            for m_id in matched_ids:
                d_ref = db_ref.document(m_id).get()
                if d_ref.exists:
                    docs_data.append((m_id, d_ref.to_dict()))
            
        if not docs_data:
            send_message(chat_id, "🔍 Veritabanında sorduğunuz konuyla ilgili eşleşen makine kaydı bulunamadı.")
            return
            
        send_message(chat_id, f"🔄 {scope_desc} kayıtlar analiz ediliyor ve yapay zeka yanıtı hazırlanıyor... (Gemma HIGH Thinking devrede, 1-3 dakika sürebilir)")
        
        context_str = ""
        for d_id, d_data in docs_data:
            context_str += f"### Makine: {d_id}\n"
            for idx, o in enumerate(d_data.get("olaylar", [])):
                context_str += f"Olay {idx+1}:\n- Neden: {o.get('neden')}\n- Çözüm: {o.get('cozum')}\n"
            context_str += "\n"
            
        system_prompt = f"""
Sen bir tütün üretim tesisi arıza ve bakım uzmanısın. Aşağıdaki kurallara göre cevap vereceksin:

1. **Doğrudan Alıntı Kuralı:** Kullanıcı bir arıza veya durum sorduğunda, veritabanındaki ilgili eşleşen "Olay" bloklarını (Neden ve Çözüm başlıklarını) KESİNLİKLE DEĞİŞTİRMEDEN, aynen alıntılayarak listele.
2. **Kendi Yorumun:** Aynen alıntıları verdikten sonra, en alta "🤖 Uzman Görüşü & Öneri" başlığı açıp bu arızanın kök sebebi, alınabilecek kalıcı mekanik/elektronik önlemler ve bakım tavsiyelerin hakkında kendi teknik yorumunu ve analizini ekle.

Veritabanı Kayıtları:
{context_str}

Kullanıcı Sorusu: {text}

Cevaplama Formatı Şablonu (HTML etiketlerine uygun olmalı: <b>, <i>, <code> kullanabilirsin, markdown ** yıldızları kullanma HTML parse modu aktif):
---
### 🛠️ Eşleşen Kayıtlar (Birebir Alıntı)
- <b>Neden:</b> [Veritabanındaki Neden metni]
- <b>Çözüm:</b> [Veritabanındaki Çözüm metni]

---
🤖 <b>Uzman Görüşü & Öneri</b>
[Senin arıza hakkındaki teknik yorumun, önleyici bakım tavsiyelerin ve tecrübene dayanan önerilerin]
---
"""
        ai_response = call_gemini(system_prompt)
        
        markup = None
        if doc_id:
            markup = {
                "inline_keyboard": [
                    [{"text": "🔙 Makine Sayfasına Dön", "callback_data": f"doc:{doc_id}"}]
                ]
            }
        else:
            markup = {
                "inline_keyboard": [
                    [{"text": "🔙 Ana Menüye Dön", "callback_data": "main_menu"}]
                ]
            }
            
        send_message(chat_id, ai_response, markup)

if __name__ == "__main__":
    if not TOKEN:
        print("HATA: TELEGRAM_BOT_TOKEN eksik!")
        exit(1)
        
    payload_str = os.getenv("PAYLOAD_JSON", "{}")
    print("Gelen Payload:", payload_str, flush=True)
    try:
        update = json.loads(payload_str)
        if "message" in update:
            chat_id = update["message"]["chat"]["id"]
            if ALLOWED_CHAT_ID and str(chat_id) != ALLOWED_CHAT_ID.strip():
                print(f"Unauthorized chat access blocked: {chat_id}")
            else:
                text = update["message"].get("text", "").strip()
                if text:
                    handle_user_text(chat_id, text)
        elif "callback_query" in update:
            process_callback_query(update["callback_query"])
    except Exception as e:
        print("Worker execution error:", e)
