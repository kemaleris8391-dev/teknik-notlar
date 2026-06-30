import os
import json
import requests
from http.server import BaseHTTPRequestHandler
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(BASE_DIR, ".env")):
    load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)
else:
    load_dotenv(os.path.join(BASE_DIR, "config.env"), override=True)

ALLOWED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        try:
            update = json.loads(post_data.decode('utf-8'))
            
            # Simple Authorization Check
            chat_id = None
            if "message" in update:
                chat_id = update["message"].get("chat", {}).get("id")
            elif "callback_query" in update:
                chat_id = update["callback_query"].get("message", {}).get("chat", {}).get("id")
                
            if chat_id and ALLOWED_CHAT_ID and str(chat_id) != ALLOWED_CHAT_ID.strip():
                print(f"Unauthorized chat access blocked: {chat_id}")
                self.send_response(403)
                self.end_headers()
                return
                
            # Trigger GitHub Actions Relay
            # Hardcoded to bypass Vercel CLI missing system git env vars
            repo_owner = "kemaleris8391-dev"
            repo_slug = "teknik-notlar"
            
            if GITHUB_TOKEN and repo_owner and repo_slug:
                github_url = f"https://api.github.com/repos/{repo_owner}/{repo_slug}/dispatches"
                headers = {
                    "Accept": "application/vnd.github.v3+json",
                    "Authorization": f"token {GITHUB_TOKEN}"
                }
                payload = {
                    "event_type": "process_telegram_message",
                    "client_payload": update
                }
                
                # Saniyeler içinde GitHub'ı tetikliyor ve beklemeden Telegram'a 200 OK dönüyoruz.
                res = requests.post(github_url, json=payload, headers=headers, timeout=5)
                print(f"GitHub Dispatch Response: {res.status_code} - {res.text}")
            else:
                print(f"HATA Eksik: GITHUB_TOKEN={bool(GITHUB_TOKEN)}, repo_owner={repo_owner}, repo_slug={repo_slug}")

            # Always return 200 OK instantly to Telegram
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "relay": "github_actions"}).encode('utf-8'))
            
        except Exception as e:
            print(f"Webhook error: {e}")
            self.send_response(500)
            self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"<h1>Telegram Webhook Relay Active</h1>")
