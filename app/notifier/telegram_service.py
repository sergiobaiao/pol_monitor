import requests
from typing import List, Tuple
from ..config import Config
from ..database import Database

class TelegramService:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    def send_notification(self, text: str, chat_ids: List[str] = None) -> Tuple[bool, str]:
        token = self.config.telegram_token
        target_chats = chat_ids or self.config.telegram_chat_ids

        if not token:
            return False, "Token do Telegram Bot não configurado."
        if not target_chats:
            return False, "Nenhum Chat ID do Telegram configurado."

        api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        errors = []
        success_count = 0

        for chat_id in target_chats:
            try:
                payload = {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True
                }
                resp = requests.post(api_url, json=payload, timeout=10)
                if resp.status_code == 200:
                    success_count += 1
                else:
                    errors.append(f"Chat {chat_id}: HTTP {resp.status_code} - {resp.text}")
            except Exception as e:
                errors.append(f"Chat {chat_id}: {str(e)}")

        if success_count > 0:
            self.db.log("INFO", f"Telegram: Mensagem enviada com sucesso para {success_count} chat(s).")
            if errors:
                return True, f"Enviado para {success_count} chats, com falhas em: {'; '.join(errors)}"
            return True, f"Mensagem enviada com sucesso para {success_count} chat(s)!"
        else:
            err_msg = f"Falha no envio do Telegram: {'; '.join(errors)}"
            self.db.log("ERROR", err_msg)
            return False, err_msg
