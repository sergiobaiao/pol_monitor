import requests
from typing import List, Tuple
from ..config import Config
from ..database import Database

class OpenWaService:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    def send_notification(self, text: str, recipients: List[str] = None) -> Tuple[bool, str]:
        base_url = self.config.openwa_url.rstrip("/")
        api_key = self.config.openwa_api_key
        target_recipients = recipients or self.config.openwa_recipients

        if not base_url:
            return False, "URL do servidor OpenWA não configurada."
        if not target_recipients:
            return False, "Nenhum número/destinatário do WhatsApp configurado."

        endpoint = f"{base_url}/sendText"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        if api_key:
            headers["api_key"] = api_key
            headers["Authorization"] = f"Bearer {api_key}"

        errors = []
        success_count = 0

        for to in target_recipients:
            # Formatar para número internacional caso não tenha sufixo
            target = to
            if "@" not in target:
                clean_num = "".join(filter(str.isdigit, target))
                target = f"{clean_num}@c.us"

            payload = {
                "chatId": target,
                "text": text,
            }

            try:
                resp = requests.post(endpoint, json=payload, headers=headers, timeout=12)
                if resp.status_code in [200, 201]:
                    success_count += 1
                else:
                    errors.append(f"{to}: HTTP {resp.status_code} - {resp.text}")
            except Exception as e:
                errors.append(f"{to}: {str(e)}")

        if success_count > 0:
            self.db.log("INFO", f"OpenWA: Mensagem enviada para {success_count} destinatário(s) no WhatsApp.")
            if errors:
                return True, f"Enviado para {success_count} destinatários, erros: {'; '.join(errors)}"
            return True, f"Mensagem enviada com sucesso para {success_count} destinatário(s) no WhatsApp!"
        else:
            err_msg = f"Falha no envio via OpenWA: {'; '.join(errors)}"
            self.db.log("ERROR", err_msg)
            return False, err_msg
