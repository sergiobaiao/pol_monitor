import time
import threading
from datetime import datetime, date
from typing import Optional, Callable, Dict, Any, List

from .database import Database
from .config import Config
from .pol_client import PolClient
from .notifier.formatter import NotificationFormatter, ClassifiedItems
from .notifier.email_service import EmailService
from .notifier.telegram_service import TelegramService
from .notifier.openwa_service import OpenWaService

class Scheduler:
    def __init__(self, config: Config, db: Database, pol_client: PolClient):
        self.config = config
        self.db = db
        self.pol_client = pol_client

        self.email_service = EmailService(config, db)
        self.telegram_service = TelegramService(config, db)
        self.openwa_service = OpenWaService(config, db)

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_sync_time: Optional[datetime] = None
        self._last_classified: Optional[ClassifiedItems] = None
        self._wake_event = threading.Event()

        self.on_sync_complete: Optional[Callable[[ClassifiedItems], None]] = None
        self.on_status_change: Optional[Callable[[str], None]] = None

    @property
    def last_sync(self) -> Optional[datetime]:
        return self._last_sync_time

    @property
    def last_classified(self) -> Optional[ClassifiedItems]:
        return self._last_classified

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, daemon=True, name="SchedulerWorker")
        self._thread.start()
        self.db.log("INFO", "Agendador de monitoramento iniciado em segundo plano.")

    def stop(self):
        self._running = False
        self._wake_event.set()
        self.db.log("INFO", "Agendador de monitoramento pausado.")

    def restart_cycle(self):
        """Acorda imediatamente o agendador para aplicar um novo intervalo de tempo configurado."""
        self._wake_event.set()

    def _worker_loop(self):
        # Executar sincronização inicial após breve espera
        time.sleep(2)
        if self._running:
            self.sync_and_notify(force=False)

        while self._running:
            self._wake_event.clear()
            interval_sec = max(60, self.config.check_interval_minutes * 60)
            
            # Aguarda o intervalo ou acorda se o usuário alterar as configurações
            interrupted = self._wake_event.wait(timeout=interval_sec)
            if self._running and not interrupted:
                self.sync_and_notify(force=False)

    def sync_and_notify(self, force: bool = False) -> ClassifiedItems:
        if self.on_status_change:
            self.on_status_change("Sincronizando com POL ADV...")

        self.db.log("INFO", f"Iniciando ciclo de sincronização (force={force})...")

        # 1. Obter dados da API do POL ADV
        tasks = self.pol_client.get_tasks()
        events = self.pol_client.get_events()

        # 2. Classificar por seções de prazos
        classified = NotificationFormatter.classify_items(tasks, events)
        self._last_classified = classified
        self._last_sync_time = datetime.now()

        today_str = date.today().isoformat()

        # 3. Disparo de Notificações
        if classified.has_items:
            # A. E-mail
            if self.config.email_enabled:
                should_send = force
                if not should_send:
                    # Checar se há item novo ainda não enviado hoje por e-mail
                    for it in (classified.today + classified.tomorrow + classified.this_week + classified.overdue):
                        if not self.db.is_already_notified(it["id"], "EMAIL", "CONSOLIDATED", today_str):
                            should_send = True
                            break

                if should_send:
                    subject = f"⚖️ POL ADV: Prazos e Tarefas — {date.today().strftime('%d/%m/%Y')}"
                    html = NotificationFormatter.to_html(classified, user_name=self.config.pol_email.split('@')[0])
                    success, msg = self.email_service.send_notification(subject, html)
                    for it in (classified.today + classified.tomorrow + classified.this_week + classified.overdue):
                        self.db.record_notification(it["id"], "EMAIL", "CONSOLIDATED", ", ".join(self.config.smtp_recipients), today_str, "SUCCESS" if success else "ERROR", msg)

            # B. Telegram
            if self.config.telegram_enabled:
                should_send = force
                if not should_send:
                    for it in (classified.today + classified.tomorrow + classified.this_week + classified.overdue):
                        if not self.db.is_already_notified(it["id"], "TELEGRAM", "CONSOLIDATED", today_str):
                            should_send = True
                            break

                if should_send:
                    tg_text = NotificationFormatter.to_telegram_markdown(classified)
                    success, msg = self.telegram_service.send_notification(tg_text)
                    for it in (classified.today + classified.tomorrow + classified.this_week + classified.overdue):
                        self.db.record_notification(it["id"], "TELEGRAM", "CONSOLIDATED", ", ".join(self.config.telegram_chat_ids), today_str, "SUCCESS" if success else "ERROR", msg)

            # C. WhatsApp (OpenWA)
            if self.config.openwa_enabled:
                should_send = force
                if not should_send:
                    for it in (classified.today + classified.tomorrow + classified.this_week + classified.overdue):
                        if not self.db.is_already_notified(it["id"], "OPENWA", "CONSOLIDATED", today_str):
                            should_send = True
                            break

                if should_send:
                    wa_text = NotificationFormatter.to_openwa_text(classified)
                    success, msg = self.openwa_service.send_notification(wa_text)
                    for it in (classified.today + classified.tomorrow + classified.this_week + classified.overdue):
                        self.db.record_notification(it["id"], "OPENWA", "CONSOLIDATED", ", ".join(self.config.openwa_recipients), today_str, "SUCCESS" if success else "ERROR", msg)

        if self.on_status_change:
            self.on_status_change(f"Atualizado em {self._last_sync_time.strftime('%H:%M:%S')}")

        if self.on_sync_complete:
            self.on_sync_complete(classified)

        return classified
