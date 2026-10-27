import time
import json
import base64
import requests
from typing import Optional, Dict, Any, List
from .database import Database
from .config import Config

KEYCLOAK_TOKEN_URL = "https://keycloak.pol.adv.br/auth/realms/pol-prod/protocol/openid-connect/token"
GATEWAY_URL = "https://office-api.pol.adv.br"


class LoginError(Exception):
    pass


def keycloak_login(email: str, password: str) -> Dict[str, Any]:
    """Valida e-mail/senha direto no Keycloak do POL (grant_type=password).
    Retorna os tokens ou lança LoginError com uma mensagem para o usuário."""
    payload = {"client_id": "web_app", "grant_type": "password", "username": email, "password": password}
    try:
        resp = requests.post(KEYCLOAK_TOKEN_URL, data=payload, timeout=15)
    except requests.RequestException as e:
        raise LoginError(f"Não foi possível conectar ao POL ADV: {e}")
    if resp.status_code == 200:
        return resp.json()
    if resp.status_code == 401:
        raise LoginError("E-mail ou senha incorretos.")
    try:
        detail = resp.json().get("error_description") or resp.text
    except ValueError:
        detail = resp.text
    raise LoginError(f"O POL ADV recusou o login (HTTP {resp.status_code}): {detail}")


def jwt_claims(token: str) -> Dict[str, Any]:
    """Lê o payload do JWT (sem validar assinatura: veio direto do Keycloak via HTTPS)."""
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class PolClient:
    def __init__(self, config: Optional[Config] = None, db: Optional[Database] = None):
        self.db = db or Database()
        self.config = config or Config(self.db)
        # Tokens e senha ficam só em memória; o login acontece a cada abertura do app
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expiry: float = 0
        self._password: Optional[str] = None
        self.session = requests.Session()

    def start_session(self, tokens: Dict[str, Any], email: str, password: str):
        """Inicia a sessão com os tokens obtidos na tela de login."""
        self._password = password
        self._store_tokens(tokens)
        self.config.pol_email = email
        self.config.pol_user_id = jwt_claims(self.access_token).get("sub", "")
        if not self.config.pol_office_id:
            self._load_office()
        self.db.log("INFO", f"Login POL ADV realizado para {email}")

    def _store_tokens(self, data: Dict[str, Any]):
        self.access_token = data.get("access_token")
        self.refresh_token = data.get("refresh_token") or self.refresh_token
        self.token_expiry = time.time() + data.get("expires_in", 300)

    def authenticate(self) -> bool:
        # Se já temos token válido e faltam mais de 5 minutos para expirar
        if self.access_token and time.time() < (self.token_expiry - 300):
            return True

        if self.refresh_token and self._refresh_token():
            return True

        # Refresh expirou: refaz o login com a senha da sessão atual
        if not self._password:
            self.db.log("WARNING", "Sessão POL expirada. Faça login novamente.")
            return False
        try:
            self._store_tokens(keycloak_login(self.config.pol_email, self._password))
            self.db.log("INFO", f"Sessão POL renovada para {self.config.pol_email}")
            return True
        except LoginError as e:
            self.db.log("ERROR", f"Falha na autenticação POL: {e}")
            return False

    def _refresh_token(self) -> bool:
        try:
            payload = {
                "client_id": "web_app",
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            }
            resp = self.session.post(KEYCLOAK_TOKEN_URL, data=payload, timeout=15)
            if resp.status_code == 200:
                self._store_tokens(resp.json())
                self.db.log("INFO", "Token POL ADV renovado com sucesso.")
                return True
        except Exception as e:
            self.db.log("WARNING", f"Falha ao renovar token POL: {e}")
        return False

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "x-officeid": self.config.pol_office_id,
            "x-selecteduserid": self.config.pol_user_id,
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

    def _load_office(self):
        """Primeiro login do usuário: usa o primeiro escritório ao qual ele pertence."""
        try:
            url = f"{GATEWAY_URL}/services/lawoffice/api/office/offices"
            resp = self.session.get(url, headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}, timeout=10)
            offices = resp.json() if resp.status_code == 200 else []
            if offices:
                self.config.pol_office_id = str(offices[0]["id"])
                self.db.log("INFO", f"Escritório POL detectado: {offices[0].get('name')} (#{offices[0]['id']})")
        except Exception as e:
            self.db.log("WARNING", f"Não foi possível detectar o escritório do usuário: {e}")

    def get_tasks(self, size: int = 100) -> List[Dict[str, Any]]:
        if not self.authenticate():
            return []

        try:
            url = f"{GATEWAY_URL}/services/lawoffice/api/task/tasks?size={size}&sort=deadline,asc"
            resp = self.session.get(url, headers=self._get_headers(), timeout=15)
            if resp.status_code == 200:
                tasks = resp.json()
                # Atualizar cache SQLite
                for t in tasks:
                    t_id = f"task_{t.get('id')}"
                    title = t.get("name", "Tarefa sem título")
                    desc = t.get("description", "")
                    status = t.get("status", "PENDING")
                    priority = t.get("priority", "MEDIUM")
                    deadline = t.get("deadline")
                    self.db.upsert_cached_item(
                        item_id=t_id,
                        item_type="TASK",
                        title=title,
                        description=desc,
                        status=status,
                        priority=priority,
                        deadline=deadline,
                        raw_data=t
                    )
                self.db.log("INFO", f"{len(tasks)} tarefas sincronizadas do POL ADV.")
                return tasks
            else:
                self.db.log("ERROR", f"Erro ao buscar tarefas ({resp.status_code}): {resp.text}")
                return []
        except Exception as e:
            self.db.log("ERROR", f"Exceção ao consultar tarefas: {e}")
            return []

    def get_events(self, size: int = 100) -> List[Dict[str, Any]]:
        if not self.authenticate():
            return []

        try:
            url = f"{GATEWAY_URL}/services/lawoffice/api/calendar/events?size={size}&sort=startDate,asc"
            resp = self.session.get(url, headers=self._get_headers(), timeout=15)
            if resp.status_code == 200:
                events = resp.json()
                # Atualizar cache SQLite
                for ev in events:
                    ev_id = f"event_{ev.get('id')}"
                    title = ev.get("title", "Evento sem título")
                    desc = ev.get("description", "")
                    status = "SCHEDULED"
                    priority = "NORMAL"
                    deadline = ev.get("startDate")
                    self.db.upsert_cached_item(
                        item_id=ev_id,
                        item_type="EVENT",
                        title=title,
                        description=desc,
                        status=status,
                        priority=priority,
                        deadline=deadline,
                        raw_data=ev
                    )
                self.db.log("INFO", f"{len(events)} eventos de calendário sincronizados do POL ADV.")
                return events
            else:
                self.db.log("ERROR", f"Erro ao buscar eventos ({resp.status_code}): {resp.text}")
                return []
        except Exception as e:
            self.db.log("ERROR", f"Exceção ao consultar eventos: {e}")
            return []
