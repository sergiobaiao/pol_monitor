from typing import Optional, List
from .database import Database

class Config:
    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    # --- POL ADV Credenciais ---
    @property
    def pol_email(self) -> str:
        return self.db.get_setting("pol_email", "")

    @pol_email.setter
    def pol_email(self, val: str):
        self.db.set_setting("pol_email", val.strip())

    @property
    def pol_office_id(self) -> str:
        return self.db.get_setting("pol_office_id", "")

    @pol_office_id.setter
    def pol_office_id(self, val: str):
        self.db.set_setting("pol_office_id", val.strip())

    @property
    def pol_user_id(self) -> str:
        return self.db.get_setting("pol_user_id", "")

    @pol_user_id.setter
    def pol_user_id(self, val: str):
        self.db.set_setting("pol_user_id", val.strip())

    # --- Inicialização e Comportamento de Janelas ---
    @property
    def start_minimized(self) -> bool:
        # Padrão: True (não exibir a janela principal automaticamente)
        return self.db.get_setting("start_minimized", "true").lower() == "true"

    @start_minimized.setter
    def start_minimized(self, val: bool):
        self.db.set_setting("start_minimized", "true" if val else "false")

    @property
    def desktop_fence_enabled(self) -> bool:
        # Padrão: True (exibir o widget no desktop ao iniciar)
        return self.db.get_setting("desktop_fence_enabled", "true").lower() == "true"

    @desktop_fence_enabled.setter
    def desktop_fence_enabled(self, val: bool):
        self.db.set_setting("desktop_fence_enabled", "true" if val else "false")

    @property
    def close_to_tray(self) -> bool:
        # Padrão: True (ao clicar em fechar X, minimiza para a bandeja)
        return self.db.get_setting("close_to_tray", "true").lower() == "true"

    @close_to_tray.setter
    def close_to_tray(self, val: bool):
        self.db.set_setting("close_to_tray", "true" if val else "false")

    @property
    def minimize_to_tray(self) -> bool:
        # Padrão: True (ao clicar em minimizar _, oculta da barra de tarefas para a bandeja)
        return self.db.get_setting("minimize_to_tray", "true").lower() == "true"

    @minimize_to_tray.setter
    def minimize_to_tray(self, val: bool):
        self.db.set_setting("minimize_to_tray", "true" if val else "false")

    # --- Intervalo de Verificação ---
    @property
    def check_interval_minutes(self) -> int:
        try:
            return int(self.db.get_setting("check_interval_minutes", "60"))
        except ValueError:
            return 60

    @check_interval_minutes.setter
    def check_interval_minutes(self, val: int):
        self.db.set_setting("check_interval_minutes", str(max(1, int(val))))

    @property
    def notify_daily_hour(self) -> str:
        return self.db.get_setting("notify_daily_hour", "08:00")

    @notify_daily_hour.setter
    def notify_daily_hour(self, val: str):
        self.db.set_setting("notify_daily_hour", val.strip())

    # --- E-mail (SMTP) ---
    @property
    def email_enabled(self) -> bool:
        return self.db.get_setting("email_enabled", "false").lower() == "true"

    @email_enabled.setter
    def email_enabled(self, val: bool):
        self.db.set_setting("email_enabled", "true" if val else "false")

    @property
    def smtp_host(self) -> str:
        return self.db.get_setting("smtp_host", "smtp.gmail.com")

    @smtp_host.setter
    def smtp_host(self, val: str):
        self.db.set_setting("smtp_host", val.strip())

    @property
    def smtp_port(self) -> int:
        try:
            return int(self.db.get_setting("smtp_port", "587"))
        except ValueError:
            return 587

    @smtp_port.setter
    def smtp_port(self, val: int):
        self.db.set_setting("smtp_port", str(val))

    @property
    def smtp_user(self) -> str:
        return self.db.get_setting("smtp_user", "")

    @smtp_user.setter
    def smtp_user(self, val: str):
        self.db.set_setting("smtp_user", val.strip())

    @property
    def smtp_pass(self) -> str:
        return self.db.get_setting("smtp_pass", "")

    @smtp_pass.setter
    def smtp_pass(self, val: str):
        self.db.set_setting("smtp_pass", val)

    @property
    def smtp_from(self) -> str:
        return self.db.get_setting("smtp_from", "")

    @smtp_from.setter
    def smtp_from(self, val: str):
        self.db.set_setting("smtp_from", val.strip())

    @property
    def smtp_recipients(self) -> List[str]:
        raw = self.db.get_setting("smtp_recipients", "")
        if not raw:
            return []
        return [r.strip() for r in raw.replace(";", ",").split(",") if r.strip()]

    @smtp_recipients.setter
    def smtp_recipients(self, val: str):
        self.db.set_setting("smtp_recipients", val.strip())

    @property
    def smtp_use_ssl(self) -> bool:
        return self.db.get_setting("smtp_use_ssl", "false").lower() == "true"

    @smtp_use_ssl.setter
    def smtp_use_ssl(self, val: bool):
        self.db.set_setting("smtp_use_ssl", "true" if val else "false")

    # --- Telegram ---
    @property
    def telegram_enabled(self) -> bool:
        return self.db.get_setting("telegram_enabled", "false").lower() == "true"

    @telegram_enabled.setter
    def telegram_enabled(self, val: bool):
        self.db.set_setting("telegram_enabled", "true" if val else "false")

    @property
    def telegram_token(self) -> str:
        return self.db.get_setting("telegram_token", "")

    @telegram_token.setter
    def telegram_token(self, val: str):
        self.db.set_setting("telegram_token", val.strip())

    @property
    def telegram_chat_ids(self) -> List[str]:
        raw = self.db.get_setting("telegram_chat_ids", "")
        if not raw:
            return []
        return [c.strip() for c in raw.replace(";", ",").split(",") if c.strip()]

    @telegram_chat_ids.setter
    def telegram_chat_ids(self, val: str):
        self.db.set_setting("telegram_chat_ids", val.strip())

    # --- WhatsApp (OpenWA) ---
    @property
    def openwa_enabled(self) -> bool:
        return self.db.get_setting("openwa_enabled", "false").lower() == "true"

    @openwa_enabled.setter
    def openwa_enabled(self, val: bool):
        self.db.set_setting("openwa_enabled", "true" if val else "false")

    @property
    def openwa_url(self) -> str:
        return self.db.get_setting("openwa_url", "http://localhost:8080")

    @openwa_url.setter
    def openwa_url(self, val: str):
        self.db.set_setting("openwa_url", val.strip().rstrip("/"))

    @property
    def openwa_api_key(self) -> str:
        return self.db.get_setting("openwa_api_key", "")

    @openwa_api_key.setter
    def openwa_api_key(self, val: str):
        self.db.set_setting("openwa_api_key", val.strip())

    @property
    def openwa_recipients(self) -> List[str]:
        raw = self.db.get_setting("openwa_recipients", "")
        if not raw:
            return []
        return [w.strip() for w in raw.replace(";", ",").split(",") if w.strip()]

    @openwa_recipients.setter
    def openwa_recipients(self, val: str):
        self.db.set_setting("openwa_recipients", val.strip())
