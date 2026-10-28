"""
POL ADV — Web & Cloudflare Worker Edition (app-web.py)
Compatível com:
1. Execução local: python app-web.py (inicia servidor em http://127.0.0.1:8080)
2. Cloudflare Workers (Python Workers com Pyodide na borda)
"""

import sys
import os
import json
import time
import asyncio
import threading
import html
import hashlib
import secrets
from datetime import datetime, date, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple
import urllib.request
import urllib.parse
import urllib.error
import base64
from email.message import EmailMessage

# Identificar se está rodando no ambiente Cloudflare Workers (Pyodide)
IS_CLOUDFLARE = False
try:
    from workers import WorkerEntrypoint, Response as CFResponse, fetch as cf_fetch
    IS_CLOUDFLARE = True
except ImportError:
    WorkerEntrypoint = object

# Prazos são do Brasil; o Worker roda em UTC (Brasília não tem mais horário de verão)
TZ = timezone(timedelta(hours=-3))
# Monitor 24h: o cron do Worker (wrangler.toml) e o loop local rodam a cada MONITOR_TICK segundos;
# cada usuário só é sincronizado quando passa o check_interval_minutes dele.
MONITOR_TICK = 300

# =============================================================================
# CONSTANTES E CONFIGURAÇÃO DEFAULT
# =============================================================================
KEYCLOAK_TOKEN_URL = "https://keycloak.pol.adv.br/auth/realms/pol-prod/protocol/openid-connect/token"
GATEWAY_URL = "https://office-api.pol.adv.br"

# Configuração inicial de cada usuário (o Office ID é detectado no primeiro login)
DEFAULT_CONFIG = {
    "pol_office_id": "",
    "check_interval_minutes": 60,
    # Notificações
    "email_enabled": False,
    "email_recipients": "",  # vazio = envia para o próprio Gmail conectado
    "telegram_enabled": False,
    "telegram_token": "",
    "telegram_chat_ids": "",
    "openwa_enabled": False,
    "openwa_url": "http://localhost:8080",
    "openwa_api_key": "",
    "openwa_recipients": ""
}

# Estado persistido (data/web_store.json local, KV `POL_STORE` no Cloudflare). Senha nunca é salva:
#   users:    {sub: {"email", "name", "config", "refresh_token" (só se marcou "Lembrar")}}
#   sessions: {sha256(cookie): sub}  -> sessões lembradas, sobrevivem a reinícios
STORE: Dict[str, Any] = {"users": {}, "sessions": {}}
# Só em memória: tokens das sessões abertas {sha256(cookie): {"sub", "access_token", "refresh_token", "expiry"}}
SESSIONS: Dict[str, Dict[str, Any]] = {}
LOGS: Dict[str, List[Dict[str, Any]]] = {}
# Usuário da requisição (ou do cron) em andamento; o servidor processa uma requisição por vez
CTX: Dict[str, Any] = {"sub": None, "auth": None}

SESSION_COOKIE = "pol_sid"
REMEMBER_SECONDS = 30 * 24 * 3600
STORE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "web_store.json")

def load_store():
    if not IS_CLOUDFLARE and os.path.exists(STORE_FILE):
        with open(STORE_FILE, encoding="utf-8") as f:
            STORE.update(json.load(f))

def save_store():
    # No Cloudflare o STORE vai para o KV ao fim de cada POST (ver on_fetch/on_scheduled)
    if not IS_CLOUDFLARE:
        os.makedirs(os.path.dirname(STORE_FILE), exist_ok=True)
        with open(STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(STORE, f, ensure_ascii=False)

def user_cfg() -> Dict[str, Any]:
    return STORE["users"][CTX["sub"]]["config"]

def log(level: str, msg: str):
    entry = {
        "timestamp": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "level": level.upper(),
        "message": msg
    }
    logs = LOGS.setdefault(CTX["sub"] or "_sistema", [])
    logs.insert(0, entry)
    del logs[100:]
    print(f"[{entry['timestamp']}] [{entry['level']}] {msg}")

# =============================================================================
# COMUNICAÇÃO HTTP RESILIENTE
# =============================================================================
async def http_request(url: str, method: str = "GET", headers: Optional[Dict[str, str]] = None, data: Optional[bytes] = None) -> Tuple[int, Dict[str, Any], bytes]:
    req_headers = headers or {}
    if IS_CLOUDFLARE:
        # urllib não tem sockets no Pyodide: no Worker toda saída HTTP passa pelo fetch()
        try:
            resp = await cf_fetch(url, method=method, headers=req_headers, body=data.decode("utf-8") if data else None)
            return resp.status, {}, (await resp.text()).encode("utf-8")
        except Exception as e:
            return 0, {}, str(e).encode("utf-8")
    # ponytail: urllib bloqueante; o servidor local atende uma requisição por vez mesmo
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            resp_headers = dict(resp.getheaders())
            return resp.status, resp_headers, body
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), e.read()
    except Exception as e:
        return 0, {}, str(e).encode("utf-8")

# =============================================================================
# CLIENTE POL ADV (Keycloak + Gateway)
# =============================================================================
async def keycloak_token(form: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    payload = urllib.parse.urlencode({"client_id": "web_app", **form}).encode("utf-8")
    status, _, body = await http_request(KEYCLOAK_TOKEN_URL, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"}, data=payload)
    try:
        return status, json.loads(body.decode("utf-8"))
    except Exception:
        return status, {"error_description": body.decode("utf-8", errors="ignore")}

def jwt_claims(token: str) -> Dict[str, Any]:
    """Payload do JWT (sem validar assinatura: veio direto do Keycloak via HTTPS)."""
    import base64
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))

async def pol_login(email: str, password: str, remember: bool) -> Tuple[Optional[Dict[str, Any]], str]:
    """Valida e-mail/senha no Keycloak. Retorna (tokens, "") ou (None, mensagem de erro)."""
    form = {"grant_type": "password", "username": email, "password": password}
    status, data = 0, {}
    if remember:
        # offline_access: refresh token de longa duração para o cron rodar sem sessão aberta
        status, data = await keycloak_token({**form, "scope": "openid offline_access"})
    if not remember or (status == 400 and data.get("error") == "invalid_scope"):
        status, data = await keycloak_token(form)
    if status == 200:
        return data, ""
    if status == 401:
        return None, "E-mail ou senha incorretos."
    if status == 0:
        return None, f"Não foi possível conectar ao POL ADV: {data.get('error_description', '')}"
    return None, f"O POL ADV recusou o login (HTTP {status}): {data.get('error_description', '')}"

def set_tokens(auth: Dict[str, Any], data: Dict[str, Any]):
    auth["access_token"] = data.get("access_token")
    auth["refresh_token"] = data.get("refresh_token") or auth.get("refresh_token")
    auth["expiry"] = time.time() + data.get("expires_in", 300)
    user = STORE["users"].get(auth["sub"])
    if user and user.get("refresh_token") and user["refresh_token"] != auth["refresh_token"]:
        user["refresh_token"] = auth["refresh_token"]  # lembrado: guarda o token renovado
        save_store()

async def pol_authenticate() -> bool:
    auth = CTX["auth"]
    if not auth:
        return False
    # Se já tem token válido
    if auth.get("access_token") and time.time() < (auth.get("expiry", 0) - 300):
        return True
    if auth.get("refresh_token"):
        status, data = await keycloak_token({"grant_type": "refresh_token", "refresh_token": auth["refresh_token"]})
        if status == 200:
            set_tokens(auth, data)
            return True
        log("WARNING", f"Falha ao renovar sessão POL (HTTP {status}). Faça login novamente.")
    return False

def pol_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {CTX['auth']['access_token']}",
        "x-officeid": str(user_cfg()["pol_office_id"]),
        "x-selecteduserid": CTX["sub"],
        "Accept": "application/json"
    }

async def pol_detect_office():
    """Primeiro login do usuário: usa o primeiro escritório ao qual ele pertence."""
    status, _, body = await http_request(f"{GATEWAY_URL}/services/lawoffice/api/office/offices",
                                   headers={"Authorization": f"Bearer {CTX['auth']['access_token']}", "Accept": "application/json"})
    try:
        offices = json.loads(body.decode("utf-8")) if status == 200 else []
        if offices:
            user_cfg()["pol_office_id"] = str(offices[0]["id"])
            log("INFO", f"Escritório POL detectado: {offices[0].get('name')} (#{offices[0]['id']})")
    except Exception as e:
        log("WARNING", f"Não foi possível detectar o escritório: {e}")

async def pol_get_tasks() -> List[Dict[str, Any]]:
    if not await pol_authenticate():
        return []
    url = f"{GATEWAY_URL}/services/lawoffice/api/task/tasks?size=100&sort=deadline,asc"
    status, _, body = await http_request(url, headers=pol_headers())
    if status == 200:
        try:
            tasks = json.loads(body.decode("utf-8"))
            log("INFO", f"{len(tasks)} tarefas sincronizadas do POL.")
            return tasks
        except Exception as e:
            log("ERROR", f"Erro parse tarefas: {e}")
    else:
        log("ERROR", f"Erro busca tarefas HTTP {status}")
    return []

async def pol_get_events() -> List[Dict[str, Any]]:
    if not await pol_authenticate():
        return []
    url = f"{GATEWAY_URL}/services/lawoffice/api/calendar/events?size=100&sort=startDate,asc"
    status, _, body = await http_request(url, headers=pol_headers())
    if status == 200:
        try:
            events = json.loads(body.decode("utf-8"))
            log("INFO", f"{len(events)} eventos de agenda sincronizados.")
            return events
        except Exception as e:
            log("ERROR", f"Erro parse eventos: {e}")
    else:
        log("ERROR", f"Erro busca eventos HTTP {status}")
    return []

# =============================================================================
# E-MAIL VIA GMAIL API (OAuth do próprio usuário; o Worker não tem SMTP)
# =============================================================================
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GMAIL_SCOPES = "openid email https://www.googleapis.com/auth/gmail.send"
GMAIL_CALLBACK = "/oauth/google/callback"

def gmail_data() -> Dict[str, Any]:
    """{client_id, client_secret, refresh_token, email, state} do usuário atual."""
    return STORE["users"][CTX["sub"]].setdefault("gmail", {})

async def google_token(form: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    status, _, body = await http_request(GOOGLE_TOKEN_URL, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"},
                                         data=urllib.parse.urlencode(form).encode("utf-8"))
    try:
        return status, json.loads(body.decode("utf-8"))
    except Exception:
        return status, {"error_description": body.decode("utf-8", errors="ignore")}

def gmail_auth_url(origin: str) -> str:
    g = gmail_data()
    g["state"] = secrets.token_urlsafe(16)  # anti-CSRF, conferido no callback
    save_store()
    return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": g["client_id"], "redirect_uri": origin + GMAIL_CALLBACK, "response_type": "code",
        "scope": GMAIL_SCOPES, "access_type": "offline", "prompt": "consent", "state": g["state"],
    })

async def gmail_callback(origin: str, params: Dict[str, str]) -> str:
    """Troca o code pelo refresh token. Retorna "" ou a mensagem de erro."""
    g = gmail_data()
    if not params.get("state") or params.get("state") != g.pop("state", None):
        return "Sessão de autorização inválida. Clique em Conectar Gmail novamente."
    if params.get("error"):
        return f"Autorização negada no Google ({params['error']})."
    status, data = await google_token({
        "grant_type": "authorization_code", "code": params.get("code", ""), "redirect_uri": origin + GMAIL_CALLBACK,
        "client_id": g.get("client_id", ""), "client_secret": g.get("client_secret", ""),
    })
    if status != 200 or not data.get("refresh_token"):
        return f"O Google recusou a troca do código (HTTP {status}): {data.get('error_description') or data.get('error', '')}"
    g["refresh_token"] = data["refresh_token"]
    g["email"] = jwt_claims(data["id_token"]).get("email", "") if data.get("id_token") else ""
    log("INFO", f"Gmail conectado: {g['email']}")
    return ""

async def send_email(subject: str, html_body: str, text_body: str) -> Tuple[bool, str]:
    g = gmail_data()
    if not g.get("refresh_token"):
        return False, "E-mail: Gmail não conectado."
    raw = user_cfg().get("email_recipients", "")
    recipients = [r.strip() for r in raw.replace(";", ",").split(",") if r.strip()] or [g.get("email")]

    status, tok = await google_token({"grant_type": "refresh_token", "refresh_token": g["refresh_token"],
                                      "client_id": g["client_id"], "client_secret": g["client_secret"]})
    if status != 200:
        if tok.get("error") == "invalid_grant":
            g.pop("refresh_token", None)
            save_store()
            log("WARNING", "Autorização do Gmail expirou ou foi revogada. Clique em Conectar Gmail novamente.")
        return False, f"E-mail: falha ao renovar acesso ao Gmail ({tok.get('error', status)})."

    msg = EmailMessage()
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    payload = json.dumps({"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")}).encode("utf-8")
    st, _, body = await http_request(GMAIL_SEND_URL, method="POST", data=payload,
                                     headers={"Authorization": f"Bearer {tok['access_token']}", "Content-Type": "application/json"})
    if st == 200:
        log("INFO", f"E-mail enviado via Gmail para {len(recipients)} destinatário(s).")
        return True, f"E-mail enviado para {', '.join(recipients)}!"
    log("ERROR", f"Gmail API HTTP {st}: {body.decode('utf-8', errors='ignore')[:200]}")
    return False, f"E-mail: Gmail recusou o envio (HTTP {st})."

# =============================================================================
# CLASSIFICADOR DE PRAZOS POR SEÇÕES
# =============================================================================
def parse_date(val: Any) -> Optional[datetime]:
    if not val:
        return None
    if isinstance(val, (int, float)):
        if val > 10000000000:
            val = val / 1000
        return datetime.fromtimestamp(val, TZ).replace(tzinfo=None)
    if isinstance(val, str):
        # Tentar ISO padrão
        clean = val.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(clean)
            return dt.astimezone(TZ).replace(tzinfo=None) if dt.tzinfo else dt
        except Exception:
            try:
                parts = val.split("T")[0].split("-")
                return datetime(int(parts[0]), int(parts[1]), int(parts[2]))
            except Exception:
                return None
    return None

def classify_items(tasks: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    today = datetime.now(TZ).date()
    next_2_days = today + timedelta(days=2)
    next_week = today + timedelta(days=7)

    res = {
        "today": [],
        "tomorrow": [],
        "this_week": [],
        "overdue": []
    }

    for t in tasks:
        if t.get("isArchived") or t.get("status") in ["FINISHED", "CONCLUDED", "DONE"]:
            continue
        dt = parse_date(t.get("deadline"))
        it = {
            "id": f"task_{t.get('id')}",
            "type": "Tarefa",
            "title": t.get("name", "Sem título"),
            "description": t.get("description") or "",
            "deadline_str": dt.strftime("%d/%m/%Y %H:%M") if dt else "Sem prazo",
            "priority": t.get("priority", "NORMAL"),
            "days": (dt.date() - today).days if dt else None,
        }
        if not dt:
            res["overdue"].append(it)
            continue

        item_d = dt.date()
        if item_d < today:
            res["overdue"].append(it)
        elif item_d == today:
            res["today"].append(it)
        elif today < item_d <= next_2_days:
            res["tomorrow"].append(it)
        elif next_2_days < item_d <= next_week:
            res["this_week"].append(it)

    for ev in events:
        dt = parse_date(ev.get("startDate"))
        if not dt:
            continue
        item_d = dt.date()
        if item_d < today:
            continue

        it = {
            "id": f"event_{ev.get('id')}",
            "type": "Audiência / Agenda",
            "title": ev.get("title", "Sem título"),
            "description": ev.get("location") or ev.get("description") or "",
            "deadline_str": dt.strftime("%d/%m/%Y %H:%M"),
            "priority": "ALTA" if "audiência" in ev.get("title", "").lower() else "NORMAL",
            "days": (item_d - today).days,
        }
        if item_d == today:
            res["today"].append(it)
        elif today < item_d <= next_2_days:
            res["tomorrow"].append(it)
        elif next_2_days < item_d <= next_week:
            res["this_week"].append(it)

    return res

# =============================================================================
# DESPACHANTES DE NOTIFICAÇÃO (TELEGRAM, OPENWA, EMAIL)
# =============================================================================
async def send_telegram(text: str) -> Tuple[bool, str]:
    cfg = user_cfg()
    token = cfg.get("telegram_token")
    raw_chats = cfg.get("telegram_chat_ids", "")
    chats = [c.strip() for c in raw_chats.replace(";", ",").split(",") if c.strip()]
    if not token or not chats:
        return False, "Telegram: Token ou Chat IDs não configurados."

    success = 0
    for cid in chats:
        payload = json.dumps({
            "chat_id": cid,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }).encode("utf-8")
        st, _, body = await http_request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=payload
        )
        if st == 200:
            success += 1

    if success > 0:
        log("INFO", f"Telegram: Mensagem enviada para {success} chat(s).")
        return True, f"Enviado para {success} chat(s) no Telegram!"
    return False, "Falha no envio via Telegram."

async def send_openwa(text: str) -> Tuple[bool, str]:
    cfg = user_cfg()
    base_url = cfg.get("openwa_url", "").rstrip("/")
    api_key = cfg.get("openwa_api_key", "")
    raw_nums = cfg.get("openwa_recipients", "")
    recipients = [n.strip() for n in raw_nums.replace(";", ",").split(",") if n.strip()]
    if not base_url or not recipients:
        return False, "OpenWA: URL ou números não configurados."

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["api_key"] = api_key

    success = 0
    for to in recipients:
        target = to if "@" in to else f"{''.join(filter(str.isdigit, to))}@c.us"
        payload = json.dumps({"chatId": target, "text": text}).encode("utf-8")
        st, _, _ = await http_request(f"{base_url}/sendText", method="POST", headers=headers, data=payload)
        if st in [200, 201]:
            success += 1

    if success > 0:
        log("INFO", f"OpenWA: Mensagem enviada para {success} destinatário(s).")
        return True, f"Enviado para {success} número(s) no WhatsApp!"
    return False, "Falha no envio via OpenWA."

# =============================================================================
# HTML / DASHBOARD WEB RESPONSIVO
# =============================================================================
LOGIN_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>POL ADV — Entrar</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="icon" type="image/png" href="https://office.pol.adv.br/content/images/pol-adv-favicon-96x96.png" />
  <style>body { background-color: #09090b; color: #f4f4f5; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }</style>
</head>
<body class="min-h-screen flex items-center justify-center p-4">
  <form id="login-form" class="bg-[#18181b] border border-[#27272a] rounded-xl p-8 w-full max-w-sm space-y-4">
    <div class="text-center">
      <h1 class="font-bold text-2xl text-blue-400">⚖️ POL ADV</h1>
      <p class="text-xs text-zinc-400 mt-1">Entre com sua conta do POL ADV</p>
    </div>
    <input type="email" id="email" autocomplete="username" required placeholder="E-mail" class="bg-zinc-800 border border-zinc-700 text-sm p-2.5 rounded w-full outline-none">
    <input type="password" id="password" autocomplete="current-password" required placeholder="Senha" class="bg-zinc-800 border border-zinc-700 text-sm p-2.5 rounded w-full outline-none">
    <label class="flex items-center gap-2 text-xs text-zinc-300"><input type="checkbox" id="remember"> Lembrar usuário/senha</label>
    <p id="login-error" class="text-xs text-red-400 min-h-[1rem]"></p>
    <button id="btn-login" class="bg-blue-600 hover:bg-blue-500 text-white text-sm font-bold py-2.5 rounded-lg w-full transition">Entrar</button>
  </form>
  <script>
    document.getElementById('login-form').onsubmit = async (e) => {
      e.preventDefault();
      const btn = document.getElementById('btn-login');
      const err = document.getElementById('login-error');
      btn.disabled = true; btn.innerText = 'Entrando...'; err.innerText = '';
      try {
        const res = await fetch('/api/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            email: document.getElementById('email').value.trim(),
            password: document.getElementById('password').value,
            remember: document.getElementById('remember').checked
          })
        });
        if (res.ok) { location.href = '/'; return; }
        err.innerText = (await res.json()).error || 'Falha no login.';
      } catch (ex) {
        err.innerText = 'Não foi possível conectar ao servidor.';
      }
      btn.disabled = false; btn.innerText = 'Entrar';
    };
  </script>
</body>
</html>
"""

INDEX_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>POL ADV — Web & Cloudflare Notifier</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="icon" type="image/png" href="https://office.pol.adv.br/content/images/pol-adv-favicon-96x96.png" />
  <style>
    body { background-color: #09090b; color: #f4f4f5; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .card-glass { background: #18181b; border: 1px solid #27272a; border-radius: 12px; }
    .active-tab { background-color: #27272a !important; color: #ffffff !important; }
  </style>
</head>
<body class="min-h-screen flex flex-col md:flex-row">

  <!-- Sidebar Lateral -->
  <aside class="w-full md:w-64 bg-[#18181b] border-r border-[#27272a] flex flex-col p-4 shrink-0">
    <div class="flex items-center gap-3 px-2 py-4 border-b border-[#27272a]">
      <span class="text-2xl">⚖️</span>
      <div>
        <h1 class="font-bold text-lg text-blue-400">POL ADV Web</h1>
        <p class="text-xs text-zinc-500">Cloudflare Workers Edition</p>
      </div>
    </div>

    <nav class="mt-6 flex flex-col gap-1.5 flex-1">
      <button onclick="setTab('dashboard')" id="tab-btn-dashboard" class="nav-btn active-tab flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-zinc-300 hover:bg-zinc-800 transition">
        <span>📊</span> <span>Widgets / Início</span>
      </button>
      <button onclick="setTab('tasks')" id="tab-btn-tasks" class="nav-btn flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-zinc-300 hover:bg-zinc-800 transition">
        <span>📋</span> <span>Tarefas</span>
      </button>
      <button onclick="setTab('calendar')" id="tab-btn-calendar" class="nav-btn flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-zinc-300 hover:bg-zinc-800 transition">
        <span>📅</span> <span>Calendário</span>
      </button>
      <button onclick="setTab('notifications')" id="tab-btn-notifications" class="nav-btn flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-zinc-300 hover:bg-zinc-800 transition">
        <span>🔔</span> <span>Notificações</span>
      </button>
      <button onclick="setTab('config')" id="tab-btn-config" class="nav-btn flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-zinc-300 hover:bg-zinc-800 transition">
        <span>⚙️</span> <span>Configurações</span>
      </button>
      <button onclick="setTab('logs')" id="tab-btn-logs" class="nav-btn flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-zinc-300 hover:bg-zinc-800 transition">
        <span>📜</span> <span>Logs & Auditoria</span>
      </button>
    </nav>

    <div class="pt-4 border-t border-[#27272a] text-xs text-zinc-500 px-2">
      <p>Tenant ID: <span class="text-zinc-300 font-mono">#{{OFFICE}}</span></p>
      <p>{{EMAIL}}</p>
      <button onclick="logout()" class="mt-2 text-zinc-400 hover:text-white">🚪 Sair / Trocar usuário</button>
    </div>
  </aside>

  <!-- Conteúdo Principal -->
  <main class="flex-1 flex flex-col min-w-0">
    <!-- Header Superior -->
    <header class="bg-[#18181b] border-b border-[#27272a] px-6 py-4 flex flex-wrap justify-between items-center gap-4">
      <div>
        <h2 id="greeting-txt" class="text-xl font-bold text-white">Olá, {{NAME}}</h2>
        <p class="text-xs text-zinc-400">Monitoramento Contínuo na Borda Cloudflare</p>
      </div>
      <div class="flex items-center gap-3">
        <span id="sync-status" class="text-xs text-emerald-400 font-medium flex items-center gap-1.5">
          <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span> Pronto
        </span>
        <button onclick="triggerSync()" id="btn-sync" class="bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold px-4 py-2 rounded-lg transition flex items-center gap-1.5">
          <span>🔄</span> <span>Sincronizar Agora</span>
        </button>
      </div>
    </header>

    <!-- Visualizações Dinâmicas -->
    <div class="p-6 flex-1 overflow-y-auto">

      <!-- ABA 1: DASHBOARD / WIDGETS -->
      <section id="view-dashboard" class="space-y-6">
        <!-- 4 Cards de Métricas (Estilo Windows 11 Widgets) -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div class="card-glass p-4 border-l-4 border-l-red-500">
            <span class="text-xs font-bold text-red-400">🚨 HOJE</span>
            <div id="count-today" class="text-3xl font-extrabold text-white mt-1">0</div>
            <p class="text-xs text-zinc-500 mt-1">Vencendo hoje</p>
          </div>
          <div class="card-glass p-4 border-l-4 border-l-amber-500">
            <span class="text-xs font-bold text-amber-400">⚠️ PRÓXIMOS 2 DIAS</span>
            <div id="count-tomorrow" class="text-3xl font-extrabold text-white mt-1">0</div>
            <p class="text-xs text-zinc-500 mt-1">Atenção necessária</p>
          </div>
          <div class="card-glass p-4 border-l-4 border-l-blue-500">
            <span class="text-xs font-bold text-blue-400">📅 ESTA SEMANA</span>
            <div id="count-week" class="text-3xl font-extrabold text-white mt-1">0</div>
            <p class="text-xs text-zinc-500 mt-1">Prazos em aberto</p>
          </div>
          <div class="card-glass p-4 border-l-4 border-l-purple-500">
            <span class="text-xs font-bold text-purple-400">⏳ ATRASADAS</span>
            <div id="count-overdue" class="text-3xl font-extrabold text-white mt-1">0</div>
            <p class="text-xs text-zinc-500 mt-1">Tarefas pendentes</p>
          </div>
        </div>

        <!-- Feeds Divididos em 2 Colunas -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div class="card-glass p-5">
            <div class="flex justify-between items-center mb-4">
              <h3 class="font-bold text-base text-zinc-100 flex items-center gap-2">📋 Prazos e Tarefas Próximas</h3>
            </div>
            <div id="feed-tasks" class="space-y-3 max-h-[420px] overflow-y-auto pr-1">
              <p class="text-xs text-zinc-500">Carregando tarefas...</p>
            </div>
          </div>

          <div class="card-glass p-5">
            <div class="flex justify-between items-center mb-4">
              <h3 class="font-bold text-base text-zinc-100 flex items-center gap-2">📅 Próximas Audiências & Eventos</h3>
            </div>
            <div id="feed-events" class="space-y-3 max-h-[420px] overflow-y-auto pr-1">
              <p class="text-xs text-zinc-500">Carregando calendário...</p>
            </div>
          </div>
        </div>
      </section>

      <!-- ABA 2: TAREFAS -->
      <section id="view-tasks" class="hidden space-y-4">
        <div class="flex flex-wrap gap-3 items-center justify-between">
          <input type="text" id="task-search" placeholder="🔍 Pesquisar em tarefas ou processos..." oninput="filterTasks()"
                 class="bg-zinc-800 border border-zinc-700 text-sm px-4 py-2 rounded-lg text-white w-full md:w-80 outline-none focus:border-blue-500">
          <div class="flex gap-1.5 bg-zinc-800 p-1 rounded-lg text-xs">
            <button onclick="setTaskFilter('all')" id="flt-all" class="px-3 py-1.5 rounded-md bg-zinc-700 text-white font-medium">Todas</button>
            <button onclick="setTaskFilter('today')" id="flt-today" class="px-3 py-1.5 rounded-md text-zinc-400 hover:text-white">Hoje</button>
            <button onclick="setTaskFilter('tomorrow')" id="flt-tomorrow" class="px-3 py-1.5 rounded-md text-zinc-400 hover:text-white">Próximos Dias</button>
            <button onclick="setTaskFilter('week')" id="flt-week" class="px-3 py-1.5 rounded-md text-zinc-400 hover:text-white">Semana</button>
            <button onclick="setTaskFilter('overdue')" id="flt-overdue" class="px-3 py-1.5 rounded-md text-zinc-400 hover:text-white">Atrasadas</button>
          </div>
        </div>
        <div id="tasks-full-list" class="card-glass p-4 space-y-3">
          <!-- Lista injetada via JS -->
        </div>
      </section>

      <!-- ABA 3: CALENDÁRIO -->
      <section id="view-calendar" class="hidden space-y-4">
        <h3 class="font-bold text-lg text-white">Eventos & Audiências do Escritório</h3>
        <div id="calendar-full-list" class="card-glass p-4 space-y-3">
          <!-- Eventos injetados via JS -->
        </div>
      </section>

      <!-- ABA 4: NOTIFICAÇÕES -->
      <section id="view-notifications" class="hidden space-y-6 max-w-4xl">
        <div class="card-glass p-5 space-y-4">
          <div class="flex justify-between items-center border-b border-zinc-800 pb-3">
            <h3 class="font-bold text-base text-zinc-100 flex items-center gap-2">✈️ Notificações por Telegram</h3>
            <button onclick="testChannel('telegram')" class="bg-zinc-800 hover:bg-zinc-700 text-xs px-3 py-1.5 rounded-md text-zinc-300 font-medium">🧪 Testar Telegram</button>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label class="text-xs text-zinc-400 font-semibold block mb-1">Bot Token:</label>
              <input type="text" id="cfg-tg-token" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
            </div>
            <div>
              <label class="text-xs text-zinc-400 font-semibold block mb-1">Chat IDs (separados por vírgula):</label>
              <input type="text" id="cfg-tg-chats" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
            </div>
          </div>
        </div>

        <div class="card-glass p-5 space-y-4">
          <div class="flex justify-between items-center border-b border-zinc-800 pb-3">
            <h3 class="font-bold text-base text-zinc-100 flex items-center gap-2">💬 WhatsApp (OpenWA / WPPConnect)</h3>
            <button onclick="testChannel('openwa')" class="bg-zinc-800 hover:bg-zinc-700 text-xs px-3 py-1.5 rounded-md text-zinc-300 font-medium">🧪 Testar WhatsApp</button>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label class="text-xs text-zinc-400 font-semibold block mb-1">URL do Servidor OpenWA:</label>
              <input type="text" id="cfg-wa-url" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
            </div>
            <div>
              <label class="text-xs text-zinc-400 font-semibold block mb-1">API Key (opcional):</label>
              <input type="text" id="cfg-wa-key" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
            </div>
            <div>
              <label class="text-xs text-zinc-400 font-semibold block mb-1">Números Destinatários:</label>
              <input type="text" id="cfg-wa-recipients" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
            </div>
          </div>
        </div>

        <div class="card-glass p-5 space-y-4">
          <div class="flex justify-between items-center border-b border-zinc-800 pb-3">
            <h3 class="font-bold text-base text-zinc-100 flex items-center gap-2">📧 E-mail via Gmail</h3>
            <button onclick="testChannel('email')" class="bg-zinc-800 hover:bg-zinc-700 text-xs px-3 py-1.5 rounded-md text-zinc-300 font-medium">🧪 Testar E-mail</button>
          </div>
          <p id="gmail-status" class="text-xs text-zinc-400">Carregando...</p>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <label class="flex items-center gap-2 text-xs text-zinc-300"><input type="checkbox" id="cfg-email-enabled"> Enviar o resumo também por e-mail</label>
            <div>
              <label class="text-xs text-zinc-400 font-semibold block mb-1">Destinatários (vazio = o próprio Gmail):</label>
              <input type="text" id="cfg-email-recipients" placeholder="a@x.com, b@y.com" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
            </div>
            <div>
              <label class="text-xs text-zinc-400 font-semibold block mb-1">ID do cliente OAuth:</label>
              <input type="text" id="gmail-client-id" placeholder="....apps.googleusercontent.com" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
            </div>
            <div>
              <label class="text-xs text-zinc-400 font-semibold block mb-1">Chave secreta do cliente:</label>
              <input type="password" id="gmail-client-secret" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
            </div>
          </div>
          <div class="flex flex-wrap gap-2">
            <button onclick="saveGmailClient()" class="bg-zinc-700 hover:bg-zinc-600 text-xs font-semibold px-3 py-2 rounded-lg">💾 Salvar cliente OAuth</button>
            <a href="/oauth/google/start" class="bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold px-3 py-2 rounded-lg">🔗 Conectar Gmail</a>
            <button onclick="disconnectGmail()" class="bg-zinc-800 hover:bg-zinc-700 text-xs px-3 py-2 rounded-lg text-zinc-300">Desconectar</button>
          </div>

          <details class="bg-zinc-900/60 border border-zinc-800 rounded-lg p-4 text-xs text-zinc-300">
            <summary class="cursor-pointer font-semibold text-zinc-100">📘 Como criar seu cliente OAuth do Gmail (uma vez só, ~10 min)</summary>
            <p class="mt-3 text-zinc-400">Cada usuário cria o próprio "app" no Google Cloud. É gratuito, não precisa de cartão, e o e-mail sai da sua própria conta Gmail. Faça login no Google com o Gmail que vai enviar os e-mails.</p>
            <ol class="list-decimal ml-5 mt-3 space-y-2">
              <li><a class="text-blue-400 underline" target="_blank" href="https://console.cloud.google.com/projectcreate">Crie um projeto</a> (ex.: <i>POL ADV Notifier</i>) e deixe-o selecionado no topo da página.</li>
              <li><a class="text-blue-400 underline" target="_blank" href="https://console.cloud.google.com/apis/library/gmail.googleapis.com">Ative a Gmail API</a> (botão <b>Ativar</b>).</li>
              <li><a class="text-blue-400 underline" target="_blank" href="https://console.cloud.google.com/auth/branding">Configure a tela de consentimento</a>: <b>Começar</b> → nome do app (<i>POL ADV Notifier</i>), seu e-mail de suporte → público <b>Externo</b> → seu e-mail de contato → aceite e <b>Criar</b>.</li>
              <li><a class="text-blue-400 underline" target="_blank" href="https://console.cloud.google.com/auth/scopes">Acesso a dados</a> → <b>Adicionar ou remover escopos</b> → marque <code>.../auth/gmail.send</code> → <b>Atualizar</b> → <b>Salvar</b>.</li>
              <li><a class="text-blue-400 underline" target="_blank" href="https://console.cloud.google.com/auth/audience">Público-alvo</a> → <b>Publicar app</b> → <b>Confirmar</b> (status <i>Em produção</i>). <span class="text-amber-400">Importante:</span> em modo "Teste" o Google desconecta o Gmail a cada 7 dias.</li>
              <li><a class="text-blue-400 underline" target="_blank" href="https://console.cloud.google.com/auth/clients/create">Crie o cliente OAuth</a>: tipo <b>Aplicativo da Web</b> → em <b>URIs de redirecionamento autorizados</b> adicione exatamente:
                <div class="flex gap-2 items-center mt-1"><code id="gmail-redirect" class="bg-black/60 px-2 py-1 rounded text-emerald-300 break-all"></code>
                <button onclick="navigator.clipboard.writeText(document.getElementById('gmail-redirect').innerText)" class="bg-zinc-800 px-2 py-1 rounded">📋 Copiar</button></div>
                → <b>Criar</b>.</li>
              <li>Copie o <b>ID do cliente</b> e a <b>Chave secreta do cliente</b> para os campos acima e clique <b>Salvar cliente OAuth</b>.</li>
              <li>Clique <b>Conectar Gmail</b>. O Google vai avisar que o app "não foi verificado": é o seu próprio app, clique <b>Avançado</b> → <b>Acessar POL ADV Notifier (não seguro)</b> → marque a permissão de <b>enviar e-mail</b> → <b>Continuar</b>.</li>
              <li>Marque <b>Enviar o resumo também por e-mail</b>, clique <b>Salvar Configurações</b> e use <b>Testar E-mail</b>.</li>
            </ol>
            <p class="mt-3 text-zinc-500">O app só pede permissão para <b>enviar</b> e-mails; ele não lê sua caixa de entrada. Para revogar: botão Desconectar ou <a class="underline" target="_blank" href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a>.</p>
          </details>
        </div>

        <button onclick="saveConfig()" class="bg-blue-600 hover:bg-blue-500 text-white font-bold text-sm px-6 py-3 rounded-lg transition w-full">
          💾 Salvar Configurações
        </button>
      </section>

      <!-- ABA 5: CONFIGURAÇÕES POL ADV -->
      <section id="view-config" class="hidden space-y-6 max-w-3xl">
        <div class="card-glass p-5 space-y-4">
          <h3 class="font-bold text-base text-zinc-100 border-b border-zinc-800 pb-3">🔐 Conta POL ADV</h3>
          <p class="text-xs text-zinc-400">Conectado como <span class="text-zinc-200">{{EMAIL}}</span></p>
          <p class="text-xs text-zinc-500">Monitoramento 24h (Telegram/WhatsApp) fica ativo enquanto o login estiver com "Lembrar usuário/senha" marcado. A checagem segue o intervalo abaixo.</p>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label class="text-xs text-zinc-400 font-semibold block mb-1">Office ID (Tenant):</label>
              <input type="text" id="cfg-pol-office" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
            </div>
            <div>
              <label class="text-xs text-zinc-400 font-semibold block mb-1">Intervalo de Checagem (minutos):</label>
              <input type="number" id="cfg-pol-interval" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
            </div>
          </div>
          <div class="flex justify-between gap-2 pt-2">
            <button onclick="sendReport()" id="btn-send-report" title="Sincroniza e envia agora o relatório para todos os canais configurados" class="bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs font-semibold px-4 py-2 rounded-lg transition">📨 Enviar relatório agora</button>
            <button onclick="saveConfig()" class="bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold px-4 py-2 rounded-lg transition">Salvar Configurações</button>
          </div>
        </div>
      </section>

      <!-- ABA 6: LOGS -->
      <section id="view-logs" class="hidden space-y-4">
        <div class="flex justify-between items-center">
          <h3 class="font-bold text-base text-white">Registro de Eventos & Auditoria</h3>
          <button onclick="loadLogs()" class="bg-zinc-800 hover:bg-zinc-700 text-xs px-3 py-1.5 rounded text-zinc-300">🔄 Atualizar Logs</button>
        </div>
        <div id="logs-box" class="bg-black/60 border border-zinc-800 rounded-lg p-4 font-mono text-xs text-zinc-300 space-y-1 h-[480px] overflow-y-auto">
          <!-- Logs inseridos aqui -->
        </div>
      </section>

    </div>
  </main>

  <script>
    // Sessão expirada: qualquer 401 da API volta para a tela de login
    const _fetch = window.fetch;
    window.fetch = async (...args) => {
      const res = await _fetch(...args);
      if (res.status === 401) location.href = '/';
      return res;
    };

    async function logout() {
      await fetch('/api/logout', { method: 'POST' });
      location.href = '/';
    }

    let currentData = { today: [], tomorrow: [], this_week: [], overdue: [] };
    let currentTaskFilter = 'all';

    function setTab(tabName) {
      ['dashboard', 'tasks', 'calendar', 'notifications', 'config', 'logs'].forEach(t => {
        document.getElementById('view-' + t).classList.add('hidden');
        document.getElementById('tab-btn-' + t).classList.remove('active-tab');
      });
      document.getElementById('view-' + tabName).classList.remove('hidden');
      document.getElementById('tab-btn-' + tabName).classList.add('active-tab');

      if (tabName === 'logs') loadLogs();
      if (tabName === 'config' || tabName === 'notifications') loadConfig();
    }

    async function triggerSync() {
      const btn = document.getElementById('btn-sync');
      const status = document.getElementById('sync-status');
      btn.innerText = '⏳ Sincronizando...';
      status.innerText = 'Sincronizando com POL ADV...';

      try {
        const res = await fetch('/api/sync', { method: 'POST' });
        const data = await res.json();
        renderDashboard(data);
        status.innerText = '🟢 Atualizado agora';
      } catch (e) {
        status.innerText = '❌ Erro ao sincronizar';
      } finally {
        btn.innerText = '🔄 Sincronizar Agora';
      }
    }

    function renderDashboard(data) {
      currentData = data;
      document.getElementById('count-today').innerText = data.today.length;
      document.getElementById('count-tomorrow').innerText = data.tomorrow.length;
      document.getElementById('count-week').innerText = data.this_week.length;
      document.getElementById('count-overdue').innerText = data.overdue.length;

      // Feed de Tarefas
      const feedTasks = document.getElementById('feed-tasks');
      feedTasks.innerHTML = '';
      const allUpcoming = [...data.today, ...data.tomorrow, ...data.this_week].filter(i => i.type === 'Tarefa');
      if (allUpcoming.length === 0) {
        feedTasks.innerHTML = '<p class="text-xs text-zinc-500 py-6 text-center">Nenhum prazo para os próximos dias.</p>';
      } else {
        allUpcoming.forEach(item => {
          feedTasks.appendChild(createItemCard(item));
        });
      }

      // Feed de Eventos
      const feedEvents = document.getElementById('feed-events');
      feedEvents.innerHTML = '';
      const allEvents = [...data.today, ...data.tomorrow, ...data.this_week].filter(i => i.type !== 'Tarefa');
      if (allEvents.length === 0) {
        feedEvents.innerHTML = '<p class="text-xs text-zinc-500 py-6 text-center">Nenhuma audiência marcada.</p>';
      } else {
        allEvents.forEach(ev => {
          feedEvents.appendChild(createItemCard(ev, true));
        });
      }

      renderTasksFullList();
      renderCalendarFullList();
    }

    function createItemCard(item, isEvent = false) {
      const div = document.createElement('div');
      div.className = 'bg-zinc-800/60 border border-zinc-700/60 p-3 rounded-lg text-xs';
      div.innerHTML = `
        <div class="flex justify-between items-center mb-1">
          <span class="font-bold px-1.5 py-0.5 rounded ${isEvent ? 'bg-blue-900/60 text-blue-300' : 'bg-amber-900/60 text-amber-300'}">${item.type}</span>
          <span class="text-zinc-400 font-mono">${item.deadline_str}</span>
        </div>
        <p class="font-semibold text-zinc-200 mt-1">${item.title}</p>
        ${item.description ? `<p class="text-zinc-500 mt-1 line-clamp-1">${item.description}</p>` : ''}
      `;
      return div;
    }

    function renderTasksFullList() {
      const box = document.getElementById('tasks-full-list');
      box.innerHTML = '';
      let list = [];
      if (currentTaskFilter === 'today') list = currentData.today;
      else if (currentTaskFilter === 'tomorrow') list = currentData.tomorrow;
      else if (currentTaskFilter === 'week') list = currentData.this_week;
      else if (currentTaskFilter === 'overdue') list = currentData.overdue;
      else list = [...currentData.today, ...currentData.tomorrow, ...currentData.this_week, ...currentData.overdue];

      list = list.filter(i => i.type === 'Tarefa');
      const q = document.getElementById('task-search').value.toLowerCase().trim();
      if (q) list = list.filter(i => i.title.toLowerCase().includes(q) || i.description.toLowerCase().includes(q));

      if (list.length === 0) {
        box.innerHTML = '<p class="text-xs text-zinc-500 text-center py-8">Nenhuma tarefa encontrada.</p>';
        return;
      }
      list.forEach(i => box.appendChild(createItemCard(i)));
    }

    function setTaskFilter(flt) {
      currentTaskFilter = flt;
      ['all', 'today', 'tomorrow', 'week', 'overdue'].forEach(f => {
        document.getElementById('flt-' + f).className = (f === flt)
          ? 'px-3 py-1.5 rounded-md bg-zinc-700 text-white font-medium'
          : 'px-3 py-1.5 rounded-md text-zinc-400 hover:text-white';
      });
      renderTasksFullList();
    }

    function filterTasks() {
      renderTasksFullList();
    }

    function renderCalendarFullList() {
      const box = document.getElementById('calendar-full-list');
      box.innerHTML = '';
      const list = [...currentData.today, ...currentData.tomorrow, ...currentData.this_week].filter(i => i.type !== 'Tarefa');
      if (list.length === 0) {
        box.innerHTML = '<p class="text-xs text-zinc-500 text-center py-8">Nenhum evento registrado na agenda.</p>';
        return;
      }
      list.forEach(i => box.appendChild(createItemCard(i, true)));
    }

    async function loadConfig() {
      const res = await fetch('/api/config');
      const cfg = await res.json();
      document.getElementById('cfg-pol-office').value = cfg.pol_office_id || '';
      document.getElementById('cfg-pol-interval').value = cfg.check_interval_minutes || '60';

      document.getElementById('cfg-tg-token').value = cfg.telegram_token || '';
      document.getElementById('cfg-tg-chats').value = cfg.telegram_chat_ids || '';
      document.getElementById('cfg-wa-url').value = cfg.openwa_url || '';
      document.getElementById('cfg-wa-key').value = cfg.openwa_api_key || '';
      document.getElementById('cfg-wa-recipients').value = cfg.openwa_recipients || '';
      document.getElementById('cfg-email-enabled').checked = !!cfg.email_enabled;
      document.getElementById('cfg-email-recipients').value = cfg.email_recipients || '';
      loadGmail();
    }

    async function loadGmail() {
      const g = await (await fetch('/api/gmail')).json();
      document.getElementById('gmail-client-id').value = g.client_id || '';
      document.getElementById('gmail-client-secret').placeholder = g.has_secret ? '•••••••• (salva)' : '';
      document.getElementById('gmail-redirect').innerText = g.redirect_uri;
      document.getElementById('gmail-status').innerHTML = g.connected
        ? '🟢 Conectado como <b>' + g.email + '</b>'
        : (g.client_id && g.has_secret ? '🟡 Cliente salvo. Clique em <b>Conectar Gmail</b>.' : '⚪ Não configurado. Veja o passo a passo abaixo.');
    }

    async function saveGmailClient() {
      await fetch('/api/gmail', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          client_id: document.getElementById('gmail-client-id').value,
          client_secret: document.getElementById('gmail-client-secret').value
        })
      });
      document.getElementById('gmail-client-secret').value = '';
      loadGmail();
    }

    async function disconnectGmail() {
      await fetch('/api/gmail/disconnect', { method: 'POST' });
      loadGmail();
    }

    async function saveConfig() {
      const payload = {
        pol_office_id: document.getElementById('cfg-pol-office').value,
        check_interval_minutes: parseInt(document.getElementById('cfg-pol-interval').value || '60'),
        telegram_token: document.getElementById('cfg-tg-token').value,
        telegram_chat_ids: document.getElementById('cfg-tg-chats').value,
        openwa_url: document.getElementById('cfg-wa-url').value,
        openwa_api_key: document.getElementById('cfg-wa-key').value,
        openwa_recipients: document.getElementById('cfg-wa-recipients').value,
        email_enabled: document.getElementById('cfg-email-enabled').checked,
        email_recipients: document.getElementById('cfg-email-recipients').value
      };
      await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      alert('Configurações salvas com sucesso!');
    }

    async function sendReport() {
      const btn = document.getElementById('btn-send-report');
      btn.disabled = true; btn.textContent = '⏳ Enviando...';
      try {
        const res = await fetch('/api/send-report', { method: 'POST' });
        const data = await res.json();
        alert((data.results || []).map(r => (r.success ? '✅ ' : '⚠️ ') + r.message).join('\\n') || (data.error || 'Nada enviado.'));
      } catch (e) {
        alert('Falha ao enviar o relatório: ' + e);
      } finally {
        btn.disabled = false; btn.textContent = '📨 Enviar relatório agora';
      }
    }

    async function testChannel(channel) {
      const res = await fetch('/api/test-channel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel })
      });
      const data = await res.json();
      alert(data.message);
    }

    async function loadLogs() {
      const res = await fetch('/api/logs');
      const logs = await res.json();
      const box = document.getElementById('logs-box');
      box.innerHTML = '';
      logs.forEach(l => {
        const p = document.createElement('p');
        p.innerText = `[${l.timestamp}] [${l.level}] ${l.message}`;
        box.appendChild(p);
      });
    }

    window.onload = function() {
      triggerSync();
      // Volta do Google após "Conectar Gmail"
      const gm = new URLSearchParams(location.search).get('gmail');
      if (gm) {
        history.replaceState(null, '', '/');
        setTab('notifications');
        alert(gm === 'ok' ? 'Gmail conectado com sucesso!' : gm);
      }
    };
  </script>
</body>
</html>
"""

# =============================================================================
# ROTEADOR DE REQUISIÇÕES (API & STATIC)
# =============================================================================
def json_response(status: int, obj: Any, headers: Optional[List[Tuple[str, str]]] = None) -> Tuple[int, str, bytes, List[Tuple[str, str]]]:
    return status, "application/json", json.dumps(obj, ensure_ascii=False).encode("utf-8"), headers or []

def session_cookie(sid: str, max_age: Optional[int]) -> Tuple[str, str]:
    cookie = f"{SESSION_COOKIE}={sid}; Path=/; HttpOnly; SameSite=Lax"
    if max_age is not None:
        cookie += f"; Max-Age={max_age}"  # sem Max-Age: some ao fechar o navegador
    if IS_CLOUDFLARE:
        cookie += "; Secure"
    return ("Set-Cookie", cookie)

async def resolve_session(cookie_header: str) -> Optional[str]:
    """Carrega o usuário do cookie em CTX. Retorna o hash da sessão ou None se não logado."""
    sid = ""
    for part in (cookie_header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == SESSION_COOKIE:
            sid = value
    if not sid:
        return None
    key = hashlib.sha256(sid.encode()).hexdigest()
    auth = SESSIONS.get(key)
    if not auth:
        # Sessão lembrada de antes de um reinício: reabre com o refresh token salvo
        sub = STORE["sessions"].get(key)
        user = STORE["users"].get(sub) if sub else None
        if not user or not user.get("refresh_token"):
            return None
        auth = {"sub": sub, "refresh_token": user["refresh_token"], "expiry": 0}
        CTX["sub"], CTX["auth"] = sub, auth
        if not await pol_authenticate():
            STORE["sessions"].pop(key, None)
            save_store()
            return None
        SESSIONS[key] = auth
    CTX["sub"], CTX["auth"] = auth["sub"], auth
    return key

async def do_login(body_bytes: bytes):
    try:
        req = json.loads(body_bytes.decode("utf-8"))
        email, password, remember = req["email"].strip(), req["password"], bool(req.get("remember"))
    except Exception:
        return json_response(400, {"error": "Informe e-mail e senha."})
    if not email or not password:
        return json_response(400, {"error": "Informe e-mail e senha."})

    tokens, error = await pol_login(email, password, remember)
    if not tokens:
        return json_response(401, {"error": error})

    claims = jwt_claims(tokens["access_token"])
    sub = claims["sub"]
    user = STORE["users"].setdefault(sub, {"config": dict(DEFAULT_CONFIG)})
    for k, v in DEFAULT_CONFIG.items():
        user["config"].setdefault(k, v)
    user["email"] = email
    user["name"] = claims.get("name") or email

    sid = secrets.token_urlsafe(32)
    key = hashlib.sha256(sid.encode()).hexdigest()
    if remember:
        user["refresh_token"] = tokens.get("refresh_token")
        STORE["sessions"][key] = sub
    else:
        # Desmarcou "Lembrar": esquece refresh token e sessões lembradas desse usuário
        user.pop("refresh_token", None)
        STORE["sessions"] = {k: s for k, s in STORE["sessions"].items() if s != sub}

    auth = SESSIONS[key] = {"sub": sub}
    CTX["sub"], CTX["auth"] = sub, auth
    set_tokens(auth, tokens)
    if not user["config"]["pol_office_id"]:
        await pol_detect_office()
    log("INFO", f"Login realizado para {email}.")
    save_store()
    return json_response(200, {"status": "ok"}, [session_cookie(sid, REMEMBER_SECONDS if remember else None)])

def redirect(location: str) -> Tuple[int, str, bytes, List[Tuple[str, str]]]:
    return 302, "text/plain", b"", [("Location", location)]

async def handle_route(path: str, method: str, body_bytes: bytes, cookie_header: str = "", query: str = "", origin: str = "") -> Tuple[int, str, bytes, List[Tuple[str, str]]]:
    CTX["sub"], CTX["auth"] = None, None
    if path == "/api/login" and method == "POST":
        return await do_login(body_bytes)

    key = await resolve_session(cookie_header)

    # 1. Página Principal Web (ou tela de login)
    if path == "/" or path == "/index.html":
        if not key:
            return 200, "text/html; charset=utf-8", LOGIN_HTML.encode("utf-8"), []
        user = STORE["users"][CTX["sub"]]
        page = (INDEX_HTML.replace("{{EMAIL}}", html.escape(user["email"]))
                .replace("{{NAME}}", html.escape(user.get("name") or user["email"]))
                .replace("{{OFFICE}}", html.escape(str(user["config"]["pol_office_id"]))))
        return 200, "text/html; charset=utf-8", page.encode("utf-8"), []

    if path.startswith("/api/") and not key:
        return json_response(401, {"error": "Sessão expirada. Faça login novamente."})

    if path.startswith("/oauth/google/"):
        if not key:
            return redirect("/")
        if path == "/oauth/google/start":
            if not (gmail_data().get("client_id") and gmail_data().get("client_secret")):
                return redirect("/?gmail=" + urllib.parse.quote("Salve o ID e a chave secreta do cliente antes de conectar."))
            return redirect(gmail_auth_url(origin))
        if path == GMAIL_CALLBACK:
            params = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
            error = await gmail_callback(origin, params)
            save_store()
            return redirect("/?gmail=" + urllib.parse.quote(error or "ok"))

    if path == "/api/logout" and method == "POST":
        SESSIONS.pop(key, None)
        STORE["sessions"].pop(key, None)
        save_store()
        return json_response(200, {"status": "ok"}, [session_cookie("", 0)])

    # 2. Sincronização Completa
    if path == "/api/sync":
        tasks = await pol_get_tasks()
        events = await pol_get_events()
        classified = classify_items(tasks, events)
        return json_response(200, classified)

    # 2b. Envio manual do relatório (sincroniza e manda para todos os canais, ignorando a deduplicação)
    if path == "/api/send-report" and method == "POST":
        classified = classify_items(await pol_get_tasks(), await pol_get_events())
        results = await send_report(classified)
        log("INFO", "Envio manual do relatório: " + "; ".join(f"{ch}: {'ok' if ok else 'falha'}" for ch, ok, _ in results))
        return json_response(200, {"results": [{"channel": ch, "success": ok, "message": msg} for ch, ok, msg in results]})

    # 3. Configurações (só as chaves conhecidas; credenciais nunca passam por aqui)
    if path == "/api/config":
        if method == "POST":
            try:
                new_cfg = json.loads(body_bytes.decode("utf-8"))
                user_cfg().update({k: v for k, v in new_cfg.items() if k in DEFAULT_CONFIG})
                save_store()
                log("INFO", "Configurações atualizadas via Web API.")
                return json_response(200, {"status": "ok"})
            except Exception as e:
                return json_response(400, {"error": str(e)})
        return json_response(200, user_cfg())

    # 4. Gmail (OAuth com o cliente que o próprio usuário criou no Google Cloud)
    if path == "/api/gmail":
        g = gmail_data()
        if method == "POST":
            req = json.loads(body_bytes.decode("utf-8"))
            client_id = (req.get("client_id") or "").strip()
            if client_id != g.get("client_id"):
                gmail_data().clear()  # outro cliente OAuth: autorização anterior não vale mais
                g = gmail_data()
            g["client_id"] = client_id
            if (req.get("client_secret") or "").strip():
                g["client_secret"] = req["client_secret"].strip()
            save_store()
            return json_response(200, {"status": "ok"})
        return json_response(200, {
            "client_id": g.get("client_id", ""), "has_secret": bool(g.get("client_secret")),
            "connected": bool(g.get("refresh_token")), "email": g.get("email", ""),
            "redirect_uri": origin + GMAIL_CALLBACK,
        })

    if path == "/api/gmail/disconnect" and method == "POST":
        g = gmail_data()
        if g.get("refresh_token"):
            await http_request(GOOGLE_REVOKE_URL + "?token=" + urllib.parse.quote(g["refresh_token"]), method="POST",
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
        g.pop("refresh_token", None)
        g.pop("email", None)
        save_store()
        return json_response(200, {"status": "ok"})

    # 4b. Logs
    if path == "/api/logs":
        return json_response(200, LOGS.get(CTX["sub"], []))

    # 5. Teste de Canal
    if path == "/api/test-channel" and method == "POST":
        try:
            req = json.loads(body_bytes.decode("utf-8"))
            ch = req.get("channel")
            if ch == "telegram":
                ok, msg = await send_telegram("🧪 *POL ADV Web — Teste de Notificação via Cloudflare Worker*\nTeste concluído com sucesso!")
                return json_response(200, {"success": ok, "message": msg})
            elif ch == "openwa":
                ok, msg = await send_openwa("*⚖️ POL ADV Web — Teste Cloudflare*\nMensagem de teste recebida!")
                return json_response(200, {"success": ok, "message": msg})
            elif ch == "email":
                ok, msg = await send_email("🧪 POL ADV — Teste de e-mail", "<p>Teste de notificação por e-mail concluído com sucesso!</p>",
                                           "Teste de notificação por e-mail concluído com sucesso!")
                return json_response(200, {"success": ok, "message": msg})
            return json_response(400, {"error": "Canal desconhecido"})
        except Exception as e:
            return json_response(500, {"error": str(e)})

    return 404, "text/plain", b"Not Found", []

def urgency_emoji(days: Optional[int]) -> str:
    """Telegram/WhatsApp não têm cor de texto: o gradiente verde → vermelho vira um marcador por item."""
    if days is None:
        return "⚪"
    return "⛔" if days < 0 else "🔴" if days == 0 else "🟠" if days <= 2 else "🟡" if days <= 5 else "🟢"

def format_summary(classified: Dict[str, Any]) -> str:
    sections = [("🔴 Hoje", "today"), ("🟠 Próximos 2 dias", "tomorrow"), ("🟡 Esta semana", "this_week"), ("⚫ Atrasadas", "overdue")]
    lines = [f"⚖️ *POL ADV — Prazos {datetime.now(TZ).strftime('%d/%m/%Y')}*"]
    for title, key in sections:
        items = classified[key]
        if not items:
            continue
        lines.append(f"\n*{title} ({len(items)})*")
        for it in items[:10]:
            lines.append(f"{urgency_emoji(it.get('days'))} {it['deadline_str']} — {it['title']}")
        if len(items) > 10:
            lines.append(f"… e mais {len(items) - 10}")
    return "\n".join(lines)

def format_summary_html(classified: Dict[str, Any]) -> str:
    sections = [("🔴 Hoje", "today", "#ef4444"), ("🟠 Próximos 2 dias", "tomorrow", "#f59e0b"),
                ("🟡 Esta semana", "this_week", "#eab308"), ("⚫ Atrasadas", "overdue", "#71717a")]
    parts = [f"<h2 style='font-family:Segoe UI,Arial'>⚖️ POL ADV — Prazos {datetime.now(TZ).strftime('%d/%m/%Y')}</h2>"]
    for title, key, color in sections:
        items = classified[key]
        if not items:
            continue
        parts.append(f"<h3 style='font-family:Segoe UI,Arial;color:{color}'>{title} ({len(items)})</h3><ul style='font-family:Segoe UI,Arial'>")
        parts += [f"<li><b>{html.escape(it['deadline_str'])}</b> — {html.escape(it['title'])} <i>({html.escape(it['type'])})</i></li>" for it in items]
        parts.append("</ul>")
    return "".join(parts)

async def send_report(classified: Dict[str, Any]) -> List[Tuple[str, bool, str]]:
    """Envia o resumo para todos os canais configurados do usuário em CTX. Devolve (canal, ok, mensagem)."""
    text = format_summary(classified)
    results = [("Telegram", *await send_telegram(text)), ("WhatsApp", *await send_openwa(text))]
    if user_cfg().get("email_enabled"):
        results.append(("E-mail", *await send_email(f"⚖️ POL ADV: Prazos e Tarefas — {datetime.now(TZ).strftime('%d/%m/%Y')}",
                                                     format_summary_html(classified), text.replace("*", ""))))
    return results

async def run_cron() -> bool:
    """Monitor 24h: sincroniza cada usuário que marcou "Lembrar" (tem refresh token salvo)
    quando vence o intervalo dele e notifica se houver item ainda não avisado hoje.
    Retorna True se algum usuário foi processado (o STORE mudou)."""
    changed = False
    now = time.time()
    today = datetime.now(TZ).date().isoformat()
    for sub, user in list(STORE["users"].items()):
        interval = max(1, int(user["config"].get("check_interval_minutes") or 60)) * 60
        if not user.get("refresh_token") or now - user.get("last_check", 0) < interval - 30:
            continue
        changed = True
        user["last_check"] = now
        CTX["sub"], CTX["auth"] = sub, {"sub": sub, "refresh_token": user["refresh_token"], "expiry": 0}
        if not await pol_authenticate():
            continue
        tasks = await pol_get_tasks()
        events = await pol_get_events()
        classified = classify_items(tasks, events)
        ids = {it["id"] for key in ("today", "tomorrow", "this_week", "overdue") for it in classified[key]}

        # Deduplicação: um resumo por dia, e outro só se surgir item novo no mesmo dia
        notified = user.get("notified", {})
        sent_ids = set(notified.get("ids", [])) if notified.get("date") == today else set()
        if not ids - sent_ids:
            continue
        if any(ok for _, ok, _ in await send_report(classified)):
            user["notified"] = {"date": today, "ids": sorted(sent_ids | ids)}
            log("INFO", f"Monitor: resumo enviado ({len(ids - sent_ids)} item(ns) novo(s)).")
    CTX["sub"], CTX["auth"] = None, None
    if changed:
        save_store()
    return changed

async def cf_load_store(env):
    kv = getattr(env, "POL_STORE", None) if env else None
    if kv is None:
        log("WARNING", "KV POL_STORE não configurado: usuários e sessões não persistem entre execuções.")
        return
    raw = await kv.get("store")
    if raw:
        STORE.clear()
        STORE.update(json.loads(raw))

async def cf_save_store(env):
    kv = getattr(env, "POL_STORE", None) if env else None
    if kv is not None:
        await kv.put("store", json.dumps(STORE, ensure_ascii=False))

# =============================================================================
# HANDLER PARA CLOUDFLARE WORKERS (PYTHON ON WEBASSEMBLY)
# =============================================================================
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = urllib.parse.urlparse(request.url)
        method = request.method
        body = (await request.text()).encode("utf-8") if method in ["POST", "PUT"] else b""

        # ponytail: KV inteiro numa chave, última escrita vence; Durable Objects se houver muitos usuários
        await cf_load_store(self.env)
        status, content_type, response_bytes, extra_headers = await handle_route(
            url.path, method, body, request.headers.get("Cookie") or "", url.query, f"{url.scheme}://{url.netloc}")
        if method == "POST" or url.path.startswith("/oauth/"):
            await cf_save_store(self.env)

        headers = {"Content-Type": content_type, **dict(extra_headers)}
        return CFResponse(response_bytes.decode("utf-8"), status=status, headers=headers)

    async def scheduled(self, controller, env, ctx):
        """Cron Trigger (a cada 5 min): monitor de todos os usuários lembrados."""
        await cf_load_store(self.env)
        if await run_cron():
            await cf_save_store(self.env)

# =============================================================================
# SERVIDOR LOCAL DE TESTE (HTTP SERVER NATIVO)
# =============================================================================
def run_local():
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import webbrowser

    port = 8080

    # ponytail: lock global (CTX/STORE são compartilhados); por usuário se houver muitos acessos
    lock = threading.Lock()

    def monitor_loop():
        while True:
            try:
                with lock:
                    asyncio.run(run_cron())
            except Exception as e:
                log("ERROR", f"Monitor: {e}")
            time.sleep(MONITOR_TICK)

    threading.Thread(target=monitor_loop, daemon=True, name="Monitor24h").start()

    class LocalHandler(BaseHTTPRequestHandler):
        def _route(self, method, body):
            parsed = urllib.parse.urlparse(self.path)
            with lock:
                self._reply(*asyncio.run(handle_route(parsed.path, method, body, self.headers.get("Cookie", ""),
                                                      parsed.query, "http://" + self.headers.get("Host", f"127.0.0.1:{port}"))))

        def do_GET(self):
            self._route("GET", b"")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self._route("POST", self.rfile.read(length) if length > 0 else b"")

        def _reply(self, status, ctype, body, extra_headers):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            for name, value in extra_headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.end_headers()

    server = HTTPServer(("127.0.0.1", port), LocalHandler)
    url = f"http://127.0.0.1:{port}/"
    print("=" * 65)
    print(f"  POL ADV Web Edition rodando localmente!")
    print(f"  Acesse: {url}")
    print(f"  Para implantar na Cloudflare: wrangler deploy")
    print("=" * 65)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
        server.server_close()

load_store()

if __name__ == "__main__":
    run_local()
