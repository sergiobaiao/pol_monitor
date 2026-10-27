import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Tuple
from ..config import Config
from ..database import Database

class EmailService:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    def send_notification(self, subject: str, html_content: str, recipients: List[str] = None) -> Tuple[bool, str]:
        target_recipients = recipients or self.config.smtp_recipients
        if not target_recipients:
            return False, "Nenhum destinatário de e-mail configurado."

        host = self.config.smtp_host
        port = self.config.smtp_port
        user = self.config.smtp_user
        password = self.config.smtp_pass
        from_addr = self.config.smtp_from or user or "pol-notifier@local"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(target_recipients)

        part_html = MIMEText(html_content, "html", "utf-8")
        msg.attach(part_html)

        try:
            if self.config.smtp_use_ssl or port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=15)
            else:
                server = smtplib.SMTP(host, port, timeout=15)
                server.ehlo()
                server.starttls()
                server.ehlo()

            if user and password:
                server.login(user, password)

            server.sendmail(from_addr, target_recipients, msg.as_string())
            server.quit()
            
            self.db.log("INFO", f"E-mail enviado com sucesso para {len(target_recipients)} destinatários: {', '.join(target_recipients)}")
            return True, "E-mail enviado com sucesso!"
        except Exception as e:
            err_msg = f"Falha ao enviar e-mail: {str(e)}"
            self.db.log("ERROR", err_msg)
            return False, err_msg
