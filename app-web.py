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
import re
from email.message import EmailMessage
from email import policy as email_policy

# Identificar se está rodando no ambiente Cloudflare Workers (Pyodide)
IS_CLOUDFLARE = False
# Bindings do Worker (preenchido em fetch/scheduled; o SMTP usa env.SMTP = Worker pol-adv-smtp)
CF_ENV: Any = None
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
    "email_method": "oauth",  # "oauth" (Gmail API) ou "smtp" (senha de app do Google; credenciais em STORE.users[sub].smtp)
    "email_recipients": "",  # vazio = envia para o próprio Gmail conectado / o usuário SMTP
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

def migrate_channel_flags(cfg: Dict[str, Any]) -> bool:
    """Antes dos interruptores, Telegram/WhatsApp enviavam sempre que configurados: quem já tinha os dados
    continua recebendo (liga o interruptor uma vez). Devolve True se mudou algo."""
    if cfg.get("_flags_v1"):
        return False
    if cfg.get("telegram_token") and cfg.get("telegram_chat_ids"):
        cfg["telegram_enabled"] = True
    if cfg.get("openwa_url") and cfg.get("openwa_recipients"):
        cfg["openwa_enabled"] = True
    cfg["_flags_v1"] = True
    return True

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
        # urllib não tem sockets no Pyodide: no Worker toda saída HTTP passa pelo fetch().
        # O nginx do gateway do POL responde 403 a requisições sem User-Agent (o fetch do Worker não envia um).
        req_headers = {"User-Agent": "PolNotifier-Web/2.1", **req_headers}
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

async def pol_fetch_all(path: str, label: str) -> List[Dict[str, Any]]:
    """Todas as páginas (size=100&page=N); o gateway devolve só a primeira sem `page`."""
    if not await pol_authenticate():
        CTX["error"] = "Sessão do POL expirada ou inválida: saia e entre de novo."
        return []
    out: List[Dict[str, Any]] = []
    for page in range(50):
        sep = "&" if "?" in path else "?"
        status, _, body = await http_request(f"{GATEWAY_URL}{path}{sep}size=100&page={page}", headers=pol_headers())
        if status != 200:
            log("ERROR", f"Erro busca {label} HTTP {status}: {body.decode('utf-8', errors='ignore')[:120]}")
            CTX["error"] = f"O POL recusou a busca de {label} (HTTP {status})."
            return out
        try:
            arr = json.loads(body.decode("utf-8"))
        except Exception as e:
            log("ERROR", f"Erro parse {label}: {e}")
            return out
        # Guarda contra um gateway que ignore `page` e devolva sempre a mesma página
        if arr and out and arr[0].get("id") == out[0].get("id"):
            break
        out += arr
        if len(arr) < 100:
            break
    log("INFO", f"{len(out)} {label} sincronizados do POL.")
    return out

async def pol_get_tasks() -> List[Dict[str, Any]]:
    return await pol_fetch_all("/services/lawoffice/api/task/tasks?active=true&sort=deadline,asc", "tarefas")

async def pol_get_events() -> List[Dict[str, Any]]:
    return await pol_fetch_all("/services/lawoffice/api/calendar/events?sort=startDate,asc", "eventos")

def board_data(tasks: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Tarefas (todas, inclusive concluídas) e compromissos para o Kanban e o calendário mensal."""
    now = datetime.now(TZ).replace(tzinfo=None)
    iso = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%S") if dt else None
    out_tasks = []
    for t in tasks:
        if t.get("isArchived"):
            continue
        dl = parse_date(t.get("deadline"))
        status = t.get("status") or "TO_DO"
        cat = t.get("category") or {}
        out_tasks.append({
            "id": t.get("id"), "title": (t.get("name") or "").strip(), "description": (t.get("description") or "").strip(),
            "status": status, "priority": t.get("priority") or "MEDIUM", "category": cat.get("name", ""),
            "color": cat.get("color") or "#71717a", "assignee": (t.get("assignedTo") or {}).get("name", ""),
            "deadline": iso(dl), "score": t.get("score") or 0, "overdue": bool(dl and dl < now and status != "DONE"),
        })
    out_events = []
    for e in events:
        cat = e.get("category") or {}
        out_events.append({
            "id": e.get("id"), "title": (e.get("title") or "").strip(), "description": (e.get("description") or "").strip(),
            "category": cat.get("name", ""), "color": cat.get("color") or "#1c64b5", "location": e.get("location") or "",
            "start": iso(parse_date(e.get("startDate"))), "end": iso(parse_date(e.get("endDate"))), "full_day": bool(e.get("fullDay")),
        })
    return {"tasks": out_tasks, "events": out_events}

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

def smtp_data() -> Dict[str, Any]:
    return STORE["users"][CTX["sub"]].setdefault("smtp", {})

class SmtpError(Exception):
    pass

async def smtp_send_rpc(host: str, port: int, user: str, password: str, rcpts: List[str], msg: EmailMessage):
    """No Worker o Python ainda não faz TLS em sockets ("SSL/TLS via create_connection is not yet supported"):
    o envio vai para o Worker JavaScript `pol-adv-smtp` (smtp-worker/) pelo service binding SMTP.
    Caminho principal: fetch no binding (o wrapper Python aceita 1 argumento posicional + nomeados, como o
    `fetch` do SDK). O RPC (`binding.send`) falha neste runtime com 'Result should have type object not
    function', então fica só como reserva. Os erros dizem em que etapa cada caminho falhou."""
    raw = base64.b64encode(msg.as_bytes(policy=email_policy.SMTP)).decode("ascii")
    payload = json.dumps({"host": host, "port": port, "user": user, "password": password, "from": user, "to": rcpts, "raw": raw})
    binding = getattr(CF_ENV, "SMTP", None) if CF_ENV is not None else None
    if binding is None:
        raise RuntimeError("binding SMTP (Worker pol-adv-smtp) não configurado")
    try:
        resp = await binding.fetch("https://smtp.internal/send", method="POST", body=payload)
        text = await resp.text()
        if resp.status == 200:
            return
        raise SmtpError(f"{resp.status} {text}")  # o Worker auxiliar respondeu: erro do servidor SMTP (ex.: 535)
    except SmtpError:
        raise
    except Exception as e:
        fetch_err = f"{type(e).__name__} em [fetch no binding]: {e}"
    log("WARNING", f"SMTP via fetch no binding falhou ({fetch_err}); tentando RPC.")
    try:
        await binding.send(payload)
        return
    except Exception as e:
        raise RuntimeError(f"fetch: {fetch_err} | RPC: {type(e).__name__} em [RPC send]: {e}")

def smtp_send_blocking(host: str, port: int, user: str, password: str, msg: EmailMessage, rcpts: List[str]):
    import smtplib
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
        server.ehlo()
        server.starttls()
        server.ehlo()
    with server:
        server.login(user, password)
        server.send_message(msg, from_addr=user, to_addrs=rcpts)

async def send_email_smtp(subject: str, html_body: str, text_body: str) -> Tuple[bool, str]:
    """E-mail por SMTP com a senha de app do Google (alternativa ao OAuth)."""
    sm = smtp_data()
    host, port = sm.get("host") or "smtp.gmail.com", int(sm.get("port") or 465)
    user, password = sm.get("user", ""), sm.get("password", "")
    if not user or not password:
        return False, "E-mail: informe o e-mail e a senha de app do Google e salve."
    raw = user_cfg().get("email_recipients", "")
    recipients = [r.strip() for r in raw.replace(";", ",").split(",") if r.strip()] or [user]

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    try:
        if IS_CLOUDFLARE:
            await smtp_send_rpc(host, port, user, password, recipients, msg)
        else:
            await asyncio.to_thread(smtp_send_blocking, host, port, user, password, msg, recipients)
    except Exception as e:
        err = str(e)
        log("ERROR", f"SMTP {host}:{port}: {err[:200]}")
        if "535" in err or "Authentication" in err:
            return False, "E-mail: o Google recusou o usuário ou a senha de app. Gere uma nova em myaccount.google.com/apppasswords (precisa da verificação em duas etapas)."
        return False, f"E-mail: falha no envio SMTP ({err[:700]})."
    log("INFO", f"E-mail enviado via SMTP para {len(recipients)} destinatário(s).")
    return True, f"E-mail enviado para {', '.join(recipients)}!"

async def send_email(subject: str, html_body: str, text_body: str) -> Tuple[bool, str]:
    if user_cfg().get("email_method") == "smtp":
        return await send_email_smtp(subject, html_body, text_body)
    return await send_email_gmail(subject, html_body, text_body)

async def send_email_gmail(subject: str, html_body: str, text_body: str) -> Tuple[bool, str]:
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

def fmt_deadline(dt: Optional[datetime]) -> str:
    """Prazos do POL são gravados como "fim do dia" (23:59 local): a hora não significa nada e só a data aparece."""
    if dt is None:
        return "Sem prazo definido"
    end_of_day = (dt.hour, dt.minute) >= (23, 59) or (dt.hour == 0 and dt.minute == 0)
    return dt.strftime("%d/%m/%Y" if end_of_day else "%d/%m/%Y %H:%M")

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
            "deadline_str": fmt_deadline(dt),
            "deadline": dt.strftime("%Y-%m-%dT%H:%M:%S") if dt else None,
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
            "deadline_str": fmt_deadline(dt),
            "deadline": dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "priority": "ALTA" if "audiência" in ev.get("title", "").lower() else "NORMAL",
            "days": (item_d - today).days,
        }
        if item_d == today:
            res["today"].append(it)
        elif today < item_d <= next_2_days:
            res["tomorrow"].append(it)
        elif next_2_days < item_d <= next_week:
            res["this_week"].append(it)

    # Cada seção em ordem de prazo (sem prazo por último), como no desktop
    for key in res:
        res[key].sort(key=lambda i: i["deadline"] or "9999")
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
  <link rel="icon" type="image/png" href="data:image/png;base64,{{LOGO}}" />
  <style>body { background-color: #09090b; color: #f4f4f5; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }</style>
</head>
<body class="min-h-screen flex items-center justify-center p-4">
  <form id="login-form" class="bg-[#18181b] border border-[#27272a] rounded-xl p-8 w-full max-w-sm space-y-4">
    <div class="text-center">
      <img src="data:image/png;base64,{{LOGO}}" alt="POL ADV Notifier" class="w-20 h-20 mx-auto mb-2">
      <h1 class="font-bold text-2xl text-blue-400">POL ADV</h1>
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
  <link rel="icon" type="image/png" href="data:image/png;base64,{{LOGO}}" />
</head>
<body class="min-h-screen flex flex-col md:flex-row">

  <!-- Sidebar Lateral -->
  <aside class="w-full md:w-64 bg-[#18181b] border-r border-[#27272a] flex flex-col p-4 shrink-0">
    <div class="flex items-center gap-3 px-2 py-4 border-b border-[#27272a]">
      <img src="data:image/png;base64,{{LOGO}}" alt="POL ADV Notifier" class="w-10 h-10">
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

      <!-- ABA 2: TAREFAS (Lista = padrão, Kanban como em office.pol.adv.br/tasks) -->
      <section id="view-tasks" class="hidden space-y-4">
        <div class="flex gap-1.5 bg-zinc-800 p-1 rounded-lg text-xs w-fit">
          <button id="tv-list" onclick="setTasksView('list')" class="px-3 py-1.5 rounded-md bg-zinc-700 text-white font-medium">Lista</button>
          <button id="tv-kanban" onclick="setTasksView('kanban')" class="px-3 py-1.5 rounded-md text-zinc-400 hover:text-white">Kanban</button>
        </div>

        <div id="tasks-list-view" class="space-y-4">
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
        </div>

        <div id="tasks-kanban-view" class="hidden space-y-4">
          <div class="flex flex-wrap gap-3 items-center justify-between">
            <input type="text" id="kb-search" placeholder="🔍 Pesquisar no quadro..." oninput="renderKanban()"
                   class="bg-zinc-800 border border-zinc-700 text-sm px-4 py-2 rounded-lg text-white w-full md:w-80 outline-none focus:border-blue-500">
            <div class="flex gap-1.5 bg-zinc-800 p-1 rounded-lg text-xs">
              <button id="kf-all" onclick="setKanbanFilter('all')" class="px-3 py-1.5 rounded-md bg-zinc-700 text-white font-medium">Todos</button>
              <button id="kf-late" onclick="setKanbanFilter('late')" class="px-3 py-1.5 rounded-md text-zinc-400 hover:text-white">Atrasadas</button>
              <button id="kf-none" onclick="setKanbanFilter('none')" class="px-3 py-1.5 rounded-md text-zinc-400 hover:text-white">Sem prazo</button>
            </div>
          </div>
          <div id="kanban-cols" class="flex gap-4 overflow-x-auto pb-2"></div>
        </div>
      </section>

      <!-- ABA 3: CALENDÁRIO (Mês como em office.pol.adv.br/calendar, Lista com tudo do mês) -->
      <section id="view-calendar" class="hidden space-y-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex items-center gap-2">
            <button onclick="shiftMonth(-1)" class="px-3 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-lg leading-none" title="Mês anterior">‹</button>
            <h3 id="cal-title" class="font-bold text-lg text-white w-60 text-center"></h3>
            <button onclick="shiftMonth(1)" class="px-3 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-lg leading-none" title="Próximo mês">›</button>
            <button onclick="shiftMonth(0)" class="px-3 py-1.5 rounded-md bg-zinc-800 hover:bg-zinc-700 text-xs">Hoje</button>
          </div>
          <div class="flex items-center gap-3 text-xs text-zinc-400">
            <span class="flex items-center gap-1"><i class="inline-block w-2.5 h-2.5 rounded-sm" style="background:#1c64b5"></i>Pendente</span>
            <span class="flex items-center gap-1"><i class="inline-block w-2.5 h-2.5 rounded-sm" style="background:#9b1c1c"></i>Atrasada</span>
            <span class="flex items-center gap-1"><i class="inline-block w-2.5 h-2.5 rounded-sm" style="background:#2f9e44"></i>Concluída</span>
          </div>
          <div class="flex gap-1.5 bg-zinc-800 p-1 rounded-lg text-xs">
            <button id="cv-month" onclick="setCalView('month')" class="px-3 py-1.5 rounded-md bg-zinc-700 text-white font-medium">Mês</button>
            <button id="cv-list" onclick="setCalView('list')" class="px-3 py-1.5 rounded-md text-zinc-400 hover:text-white">Lista</button>
          </div>
        </div>
        <div id="cal-month-view" class="overflow-x-auto">
          <div class="min-w-[760px]">
            <div class="grid grid-cols-7 text-center text-xs text-zinc-400 mb-1">
              <div>Dom.</div><div>Seg.</div><div>Ter.</div><div>Qua.</div><div>Qui.</div><div>Sex.</div><div>Sáb.</div>
            </div>
            <div id="cal-grid" class="grid grid-cols-7"></div>
          </div>
        </div>
        <div id="cal-list-view" class="hidden card-glass p-4 space-y-2"></div>
      </section>

      <!-- ABA 4: NOTIFICAÇÕES -->
      <section id="view-notifications" class="hidden space-y-6 max-w-4xl">
        <div class="card-glass p-5 space-y-4">
          <div class="flex justify-between items-center border-b border-zinc-800 pb-3">
            <h3 class="font-bold text-base text-zinc-100 flex items-center gap-2">✈️ Notificações por Telegram</h3>
            <label class="flex items-center gap-2 text-xs text-zinc-300"><input type="checkbox" id="cfg-tg-enabled"> Ativo</label>
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
            <label class="flex items-center gap-2 text-xs text-zinc-300"><input type="checkbox" id="cfg-wa-enabled"> Ativo</label>
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
            <h3 class="font-bold text-base text-zinc-100 flex items-center gap-2">📧 E-mail</h3>
            <button onclick="testChannel('email')" class="bg-zinc-800 hover:bg-zinc-700 text-xs px-3 py-1.5 rounded-md text-zinc-300 font-medium">🧪 Testar E-mail</button>
          </div>
          <div class="flex flex-wrap gap-4 text-xs text-zinc-300">
            <label class="flex items-center gap-2"><input type="radio" name="email-method" value="oauth" onchange="setEmailMethod('oauth')" checked> OAuth do Google (Gmail API)</label>
            <label class="flex items-center gap-2"><input type="radio" name="email-method" value="smtp" onchange="setEmailMethod('smtp')"> Senha de app do Google (SMTP)</label>
          </div>

          <div id="email-smtp" class="hidden space-y-4">
            <p id="smtp-status" class="text-xs text-zinc-400">Carregando...</p>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label class="text-xs text-zinc-400 font-semibold block mb-1">E-mail (conta Google):</label>
                <input type="text" id="smtp-user" placeholder="voce@gmail.com" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
              </div>
              <div>
                <label class="text-xs text-zinc-400 font-semibold block mb-1">Senha de app (16 letras):</label>
                <input type="password" id="smtp-pass" placeholder="xxxx xxxx xxxx xxxx" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
              </div>
              <div>
                <label class="text-xs text-zinc-400 font-semibold block mb-1">Servidor SMTP:</label>
                <input type="text" id="smtp-host" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
              </div>
              <div>
                <label class="text-xs text-zinc-400 font-semibold block mb-1">Porta (465 = TLS direto, 587 = STARTTLS):</label>
                <input type="number" id="smtp-port" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
              </div>
            </div>
            <div class="flex flex-wrap gap-2">
              <button onclick="saveSmtp()" class="bg-zinc-700 hover:bg-zinc-600 text-xs font-semibold px-3 py-2 rounded-lg">💾 Salvar SMTP</button>
              <button onclick="clearSmtp()" class="bg-zinc-800 hover:bg-zinc-700 text-xs px-3 py-2 rounded-lg text-zinc-300">Remover senha</button>
            </div>
            <details class="bg-zinc-900/60 border border-zinc-800 rounded-lg p-4 text-xs text-zinc-300">
              <summary class="cursor-pointer font-semibold text-zinc-100">📘 Como gerar a senha de app do Google (1 minuto)</summary>
              <ol class="list-decimal ml-5 mt-3 space-y-2">
                <li>Ative a <a class="text-blue-400 underline" target="_blank" href="https://myaccount.google.com/signinoptions/twosv">verificação em duas etapas</a> na sua conta Google (é exigida pelo Google).</li>
                <li>Abra <a class="text-blue-400 underline" target="_blank" href="https://myaccount.google.com/apppasswords">myaccount.google.com/apppasswords</a>, dê um nome (ex.: <i>POL ADV</i>) e clique em <b>Criar</b>.</li>
                <li>Copie as 16 letras (os espaços são ignorados), cole em <b>Senha de app</b>, preencha seu e-mail e clique em <b>Salvar SMTP</b>.</li>
                <li>Marque <b>Enviar o resumo também por e-mail</b>, <b>Salvar Configurações</b> e use <b>Testar E-mail</b>.</li>
              </ol>
              <p class="mt-3 text-zinc-500">A senha fica salva só na sua conta deste app (nunca volta para o navegador) e dá acesso apenas ao envio por SMTP; para revogar, apague-a no link acima. Servidor Google: <code>smtp.gmail.com</code>, porta <code>465</code> (recomendada) ou <code>587</code>.</p>
            </details>
          </div>

          <p id="gmail-status" class="oauth-only text-xs text-zinc-400">Carregando...</p>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <label class="flex items-center gap-2 text-xs text-zinc-300"><input type="checkbox" id="cfg-email-enabled"> Enviar o resumo também por e-mail</label>
            <div>
              <label class="text-xs text-zinc-400 font-semibold block mb-1">Destinatários (vazio = o próprio Gmail):</label>
              <input type="text" id="cfg-email-recipients" placeholder="a@x.com, b@y.com" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
            </div>
            <div class="oauth-only">
              <label class="text-xs text-zinc-400 font-semibold block mb-1">ID do cliente OAuth:</label>
              <input type="text" id="gmail-client-id" placeholder="....apps.googleusercontent.com" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
            </div>
            <div class="oauth-only">
              <label class="text-xs text-zinc-400 font-semibold block mb-1">Chave secreta do cliente:</label>
              <input type="password" id="gmail-client-secret" class="bg-zinc-800 border border-zinc-700 text-xs p-2 rounded w-full outline-none">
            </div>
          </div>
          <div class="oauth-only flex flex-wrap gap-2">
            <button onclick="saveGmailClient()" class="bg-zinc-700 hover:bg-zinc-600 text-xs font-semibold px-3 py-2 rounded-lg">💾 Salvar cliente OAuth</button>
            <a href="/oauth/google/start" class="bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold px-3 py-2 rounded-lg">🔗 Conectar Gmail</a>
            <button onclick="disconnectGmail()" class="bg-zinc-800 hover:bg-zinc-700 text-xs px-3 py-2 rounded-lg text-zinc-300">Desconectar</button>
          </div>

          <details class="oauth-only bg-zinc-900/60 border border-zinc-800 rounded-lg p-4 text-xs text-zinc-300">
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

  <!-- Popup de detalhes (clique em item do calendário/Kanban) -->
  <div id="detail-modal" class="hidden fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70" onclick="if (event.target === this) closeModal()">
    <div class="bg-zinc-900 border border-zinc-700 rounded-xl w-full max-w-xl max-h-[90vh] overflow-y-auto p-5">
      <div id="modal-body"></div>
      <div class="text-right mt-4"><button onclick="closeModal()" class="bg-zinc-800 hover:bg-zinc-700 text-xs font-semibold px-4 py-2 rounded-lg">Fechar</button></div>
    </div>
  </div>

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

      if ((tabName === 'tasks' || tabName === 'calendar') && !boardLoaded) loadBoard();
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
        status.innerText = data.error ? '⚠️ ' + data.error : '🟢 Atualizado agora';
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
      loadBoard();
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

    // ---------- Kanban e calendário mensal (como office.pol.adv.br/tasks e /calendar) ----------
    let board = { tasks: [], events: [] };
    let boardLoaded = false;
    let kanbanFilter = 'all';
    let calMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
    const KB_COLS = [['TO_DO', 'A FAZER'], ['IN_PROGRESS', 'EM ANDAMENTO'], ['REVIEW', 'A REVISAR'], ['DONE', 'CONCLUÍDAS']];
    const PRIO = { HIGH: ['Alta', '↑', '#a78bfa'], MEDIUM: ['Média', '—', '#facc15'], LOW: ['Baixa', '↓', '#60a5fa'] };
    const STATUS_TXT = { TO_DO: 'A fazer', IN_PROGRESS: 'Em andamento', REVIEW: 'A revisar', DONE: 'Concluída' };
    const SEG_ON = 'px-3 py-1.5 rounded-md bg-zinc-700 text-white font-medium';
    const SEG_OFF = 'px-3 py-1.5 rounded-md text-zinc-400 hover:text-white';

    function el(tag, cls, text) {
      const e = document.createElement(tag);
      if (cls) e.className = cls;
      if (text !== undefined && text !== null) e.textContent = text;
      return e;
    }

    function parseLocal(s) {
      if (!s) return null;
      const parts = s.split('T');
      const d = parts[0].split('-').map(Number);
      const t = (parts[1] || '00:00').split(':').map(Number);
      return new Date(d[0], d[1] - 1, d[2], t[0], t[1]);
    }

    async function loadBoard() {
      try {
        const data = await (await fetch('/api/board')).json();
        data.tasks.forEach(t => { t.kind = 'task'; t.when = parseLocal(t.deadline); });
        data.events.forEach(e => { e.kind = 'event'; e.when = parseLocal(e.start); e.endWhen = parseLocal(e.end); });
        board = data;
        boardLoaded = true;
      } catch (e) { /* mantém o que já estava na tela */ }
      renderKanban();
      renderCalendar();
    }

    function itemColor(it) {
      if (it.kind === 'event') return it.color || '#1c64b5';
      return it.status === 'DONE' ? '#2f9e44' : (it.overdue ? '#9b1c1c' : '#1c64b5');
    }

    function itemState(it) {
      if (it.kind === 'event') return 'Compromisso';
      return it.status === 'DONE' ? 'Concluída' : (it.overdue ? 'Atrasada' : 'Pendente');
    }

    // Prazos de tarefa gravados como fim do dia (23:59) não têm hora de verdade
    function hasTime(it) {
      const d = it.when;
      if (!d) return false;
      if (it.kind === 'event') return !it.full_day;
      return !(d.getHours() === 23 && d.getMinutes() === 59) && !(d.getHours() === 0 && d.getMinutes() === 0);
    }

    function hhmm(d) { return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }); }

    function fmtWhen(it) {
      const d = it.when;
      if (!d) return 'Sem prazo';
      let txt = d.toLocaleDateString('pt-BR');
      if (hasTime(it)) {
        txt += ' ' + hhmm(d);
        if (it.endWhen && it.endWhen > d) txt += ' até ' + hhmm(it.endWhen);
      }
      return txt + ' (' + d.toLocaleDateString('pt-BR', { weekday: 'long' }) + ')';
    }

    function setTasksView(v) {
      document.getElementById('tv-list').className = v === 'list' ? SEG_ON : SEG_OFF;
      document.getElementById('tv-kanban').className = v === 'kanban' ? SEG_ON : SEG_OFF;
      document.getElementById('tasks-list-view').classList.toggle('hidden', v !== 'list');
      document.getElementById('tasks-kanban-view').classList.toggle('hidden', v !== 'kanban');
      if (v === 'kanban') renderKanban();
    }

    function setKanbanFilter(f) {
      kanbanFilter = f;
      ['all', 'late', 'none'].forEach(k => { document.getElementById('kf-' + k).className = k === f ? SEG_ON : SEG_OFF; });
      renderKanban();
    }

    function renderKanban() {
      const root = document.getElementById('kanban-cols');
      if (!root) return;
      root.innerHTML = '';
      const q = document.getElementById('kb-search').value.toLowerCase().trim();
      const tasks = board.tasks
        .filter(t => kanbanFilter === 'late' ? t.overdue : (kanbanFilter === 'none' ? !t.deadline : true))
        .filter(t => !q || t.title.toLowerCase().includes(q) || t.description.toLowerCase().includes(q));
      KB_COLS.forEach(col => {
        // Mesma ordem do POL: campo score crescente
        const items = tasks.filter(t => t.status === col[0]).sort((a, b) => a.score - b.score);
        const wrap = el('div', 'min-w-[270px] flex-1');
        const head = el('div', 'flex items-center gap-2 mb-2 px-1');
        head.appendChild(el('span', 'text-xs font-bold text-zinc-200', col[1]));
        head.appendChild(el('span', 'text-[11px] font-bold bg-blue-600 text-white rounded-full px-2', String(items.length)));
        wrap.appendChild(head);
        const box = el('div', 'bg-zinc-900/70 rounded-xl p-2 space-y-2 max-h-[70vh] overflow-y-auto');
        items.forEach(t => box.appendChild(kanbanCard(t)));
        if (!items.length) box.appendChild(el('p', 'text-xs text-zinc-600 text-center py-6', 'Nada aqui.'));
        wrap.appendChild(box);
        root.appendChild(wrap);
      });
    }

    function kanbanCard(t) {
      const card = el('div', 'cursor-pointer rounded-lg p-3 border hover:brightness-125 transition');
      card.style.background = t.overdue ? '#3b1d1f' : '#232327';
      card.style.borderColor = t.overdue ? '#7f1d1d' : '#2f2f35';
      card.title = 'Clique para ver os detalhes';
      card.onclick = () => showDetail(t);
      card.appendChild(el('p', 'text-sm font-semibold text-zinc-100 break-words', t.title));
      if (t.description) card.appendChild(el('p', 'text-xs text-zinc-400 mt-1 line-clamp-2 break-words', t.description));
      const badges = el('div', 'flex flex-wrap gap-1.5 mt-2');
      const pr = PRIO[t.priority];
      if (pr) {
        const b = el('span', 'text-[11px] font-bold rounded px-1.5 py-0.5 bg-zinc-800', pr[1] + ' ' + pr[0]);
        b.style.color = pr[2];
        badges.appendChild(b);
      }
      if (t.category) {
        const b = el('span', 'text-[11px] rounded px-1.5 py-0.5 border', t.category);
        b.style.color = t.color; b.style.borderColor = t.color;
        badges.appendChild(b);
      }
      card.appendChild(badges);
      const foot = el('div', 'flex items-center justify-between mt-3 text-[11px]');
      const initials = t.assignee.split(' ').filter(Boolean);
      const av = el('span', 'inline-flex items-center justify-center w-6 h-6 rounded-full bg-zinc-700 text-white font-bold text-[10px]',
        initials.length ? (initials[0][0] + (initials.length > 1 ? initials[initials.length - 1][0] : '')).toUpperCase() : '');
      av.title = t.assignee;
      foot.appendChild(av);
      const when = t.when ? t.when.toLocaleDateString('pt-BR') + (hasTime(t) ? ' - ' + hhmm(t.when) : '') : 'Sem prazo';
      foot.appendChild(el('span', t.overdue ? 'text-red-400 font-semibold' : 'text-zinc-400', (t.overdue ? '⚠ ' : '📅 ') + when));
      card.appendChild(foot);
      return card;
    }

    function setCalView(v) {
      document.getElementById('cv-month').className = v === 'month' ? SEG_ON : SEG_OFF;
      document.getElementById('cv-list').className = v === 'list' ? SEG_ON : SEG_OFF;
      document.getElementById('cal-month-view').classList.toggle('hidden', v !== 'month');
      document.getElementById('cal-list-view').classList.toggle('hidden', v !== 'list');
    }

    function shiftMonth(delta) {
      const now = new Date();
      calMonth = delta === 0 ? new Date(now.getFullYear(), now.getMonth(), 1) : new Date(calMonth.getFullYear(), calMonth.getMonth() + delta, 1);
      renderCalendar();
    }

    function calItems() {
      return board.tasks.concat(board.events).filter(i => i.when).sort((a, b) => a.when - b.when);
    }

    function dayKey(d) { return d.getFullYear() + '-' + d.getMonth() + '-' + d.getDate(); }

    function renderCalendar() {
      const title = document.getElementById('cal-title');
      if (!title) return;
      title.textContent = calMonth.toLocaleDateString('pt-BR', { month: 'long', year: 'numeric' }).toUpperCase();
      const byDay = {};
      calItems().forEach(i => { (byDay[dayKey(i.when)] = byDay[dayKey(i.when)] || []).push(i); });

      // Grade de 6 semanas começando no domingo
      const grid = document.getElementById('cal-grid');
      grid.innerHTML = '';
      const start = new Date(calMonth.getFullYear(), calMonth.getMonth(), 1 - calMonth.getDay());
      const today = new Date();
      for (let n = 0; n < 42; n++) {
        const day = new Date(start.getFullYear(), start.getMonth(), start.getDate() + n);
        const inMonth = day.getMonth() === calMonth.getMonth();
        const isToday = dayKey(day) === dayKey(today);
        const cell = el('div', 'min-h-[118px] border border-zinc-800 p-1 ' + (isToday ? 'bg-blue-950/40' : (inMonth ? 'bg-zinc-900/60' : 'bg-zinc-950/60')));
        const num = el('div', 'flex justify-end mb-1');
        num.appendChild(el('span', 'text-xs w-6 h-6 inline-flex items-center justify-center rounded-full ' + (isToday ? 'bg-blue-600 text-white font-bold' : (inMonth ? 'text-zinc-300' : 'text-zinc-600')), String(day.getDate())));
        cell.appendChild(num);
        const items = byDay[dayKey(day)] || [];
        items.slice(0, 2).forEach(it => cell.appendChild(calEntry(it, inMonth)));
        if (items.length > 2) {
          const more = el('button', 'text-[11px] font-semibold text-blue-400 hover:text-blue-300 px-1', '+' + (items.length - 2) + ' mais');
          more.onclick = () => showDay(day, items);
          cell.appendChild(more);
        }
        grid.appendChild(cell);
      }

      // Aba Lista: tudo do mês, por dia
      const list = document.getElementById('cal-list-view');
      list.innerHTML = '';
      const days = {};
      calItems().filter(i => i.when.getMonth() === calMonth.getMonth() && i.when.getFullYear() === calMonth.getFullYear())
        .forEach(i => { (days[dayKey(i.when)] = days[dayKey(i.when)] || []).push(i); });
      const keys = Object.keys(days);
      keys.forEach(k => {
        const items = days[k];
        const head = items[0].when.toLocaleDateString('pt-BR', { weekday: 'long', day: 'numeric', month: 'long' });
        list.appendChild(el('p', 'text-xs font-bold uppercase tracking-wide text-zinc-400 pt-3', head));
        items.forEach(it => list.appendChild(listRow(it)));
      });
      if (!keys.length) list.appendChild(el('p', 'text-xs text-zinc-500 text-center py-8', 'Nada neste mês.'));
    }

    function calEntry(it, inMonth) {
      const e = el('div', 'text-[11px] leading-tight text-white rounded px-1.5 py-1 mb-1 cursor-pointer hover:brightness-125');
      e.appendChild(el('div', 'line-clamp-2 break-words' + (it.status === 'DONE' ? ' line-through' : ''), (it.kind === 'event' ? '📅 ' : '') + it.title));
      e.style.background = itemColor(it);
      e.style.opacity = inMonth ? '1' : '0.55';
      e.title = it.title + ' · ' + itemState(it) + ' · ' + fmtWhen(it) + ' (clique para ver os detalhes)';
      e.onclick = () => showDetail(it);
      return e;
    }

    function listRow(it) {
      const row = el('div', 'flex gap-3 bg-zinc-800/60 hover:bg-zinc-800 border border-zinc-700/60 rounded-lg p-3 cursor-pointer');
      const sw = el('i', 'inline-block w-2.5 h-2.5 rounded-sm mt-1.5 shrink-0');
      sw.style.background = itemColor(it);
      row.appendChild(sw);
      const body = el('div', 'min-w-0');
      body.appendChild(el('p', 'text-sm font-semibold text-zinc-100 break-words' + (it.status === 'DONE' ? ' line-through' : ''), it.title));
      const meta = [itemState(it), hasTime(it) ? hhmm(it.when) : '', it.category, it.assignee].filter(Boolean).join(' · ');
      body.appendChild(el('p', 'text-[11px] text-zinc-400', meta));
      if (it.description) body.appendChild(el('p', 'text-xs text-zinc-500 mt-1 line-clamp-2 break-words', it.description));
      row.appendChild(body);
      row.onclick = () => showDetail(it);
      return row;
    }

    function showDetail(it) {
      const body = document.getElementById('modal-body');
      body.innerHTML = '';
      body.appendChild(el('h3', 'text-base font-bold text-zinc-100 break-words', it.title));
      const chip = el('span', 'inline-block mt-2 mb-3 text-[11px] font-bold text-white rounded px-2 py-0.5', itemState(it));
      chip.style.background = itemColor(it);
      body.appendChild(chip);
      const rows = [[it.kind === 'task' ? 'Prazo' : 'Data', fmtWhen(it)]];
      if (it.kind === 'task') {
        if (PRIO[it.priority]) rows.push(['Prioridade', PRIO[it.priority][0]]);
        rows.push(['Situação', STATUS_TXT[it.status] || it.status]);
      }
      rows.push(['Categoria', it.category], ['Responsável', it.assignee || ''], ['Local', it.location || '']);
      rows.filter(r => r[1]).forEach(r => {
        const line = el('div', 'flex gap-3 text-sm mb-1');
        line.appendChild(el('span', 'w-24 shrink-0 text-zinc-400', r[0]));
        line.appendChild(el('span', 'text-zinc-200 break-words', r[1]));
        body.appendChild(line);
      });
      if (it.description) {
        body.appendChild(el('p', 'text-xs text-zinc-400 mt-3 mb-1', 'Descrição'));
        body.appendChild(el('div', 'whitespace-pre-wrap break-words text-sm text-zinc-300 bg-zinc-950/60 rounded-lg p-3 max-h-72 overflow-y-auto', it.description));
      }
      document.getElementById('detail-modal').classList.remove('hidden');
    }

    function showDay(day, items) {
      const body = document.getElementById('modal-body');
      body.innerHTML = '';
      body.appendChild(el('h3', 'text-base font-bold text-zinc-100 mb-3', day.toLocaleDateString('pt-BR', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })));
      const list = el('div', 'space-y-2');
      items.forEach(it => list.appendChild(listRow(it)));
      body.appendChild(list);
      document.getElementById('detail-modal').classList.remove('hidden');
    }

    function closeModal() { document.getElementById('detail-modal').classList.add('hidden'); }
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

    async function loadConfig() {
      const res = await fetch('/api/config');
      const cfg = await res.json();
      document.getElementById('cfg-pol-office').value = cfg.pol_office_id || '';
      document.getElementById('cfg-pol-interval').value = cfg.check_interval_minutes || '60';

      document.getElementById('cfg-tg-enabled').checked = !!cfg.telegram_enabled;
      document.getElementById('cfg-wa-enabled').checked = !!cfg.openwa_enabled;
      document.getElementById('cfg-tg-token').value = cfg.telegram_token || '';
      document.getElementById('cfg-tg-chats').value = cfg.telegram_chat_ids || '';
      document.getElementById('cfg-wa-url').value = cfg.openwa_url || '';
      document.getElementById('cfg-wa-key').value = cfg.openwa_api_key || '';
      document.getElementById('cfg-wa-recipients').value = cfg.openwa_recipients || '';
      document.getElementById('cfg-email-enabled').checked = !!cfg.email_enabled;
      document.getElementById('cfg-email-recipients').value = cfg.email_recipients || '';
      const method = cfg.email_method === 'smtp' ? 'smtp' : 'oauth';
      document.querySelector('input[name="email-method"][value="' + method + '"]').checked = true;
      setEmailMethod(method);
      loadGmail();
      loadSmtp();
    }

    function emailMethod() {
      return document.querySelector('input[name="email-method"]:checked').value;
    }

    function setEmailMethod(m) {
      document.getElementById('email-smtp').classList.toggle('hidden', m !== 'smtp');
      document.querySelectorAll('.oauth-only').forEach(el => el.classList.toggle('hidden', m === 'smtp'));
    }

    async function loadSmtp() {
      const s = await (await fetch('/api/smtp')).json();
      document.getElementById('smtp-user').value = s.user || '';
      document.getElementById('smtp-host').value = s.host || 'smtp.gmail.com';
      document.getElementById('smtp-port').value = s.port || 465;
      document.getElementById('smtp-pass').placeholder = s.has_password ? '•••• (salva)' : 'xxxx xxxx xxxx xxxx';
      document.getElementById('smtp-status').innerHTML = s.user && s.has_password
        ? '🟢 Senha de app salva para <b>' + s.user + '</b>. Use <b>Testar E-mail</b>.'
        : '⚪ Não configurado. Veja o passo a passo abaixo.';
    }

    async function saveSmtp() {
      await fetch('/api/smtp', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user: document.getElementById('smtp-user').value,
          host: document.getElementById('smtp-host').value,
          port: parseInt(document.getElementById('smtp-port').value || '465'),
          password: document.getElementById('smtp-pass').value
        })
      });
      document.getElementById('smtp-pass').value = '';
      loadSmtp();
    }

    async function clearSmtp() {
      await fetch('/api/smtp', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ clear: true }) });
      loadSmtp();
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
        telegram_enabled: document.getElementById('cfg-tg-enabled').checked,
        openwa_enabled: document.getElementById('cfg-wa-enabled').checked,
        telegram_token: document.getElementById('cfg-tg-token').value,
        telegram_chat_ids: document.getElementById('cfg-tg-chats').value,
        openwa_url: document.getElementById('cfg-wa-url').value,
        openwa_api_key: document.getElementById('cfg-wa-key').value,
        openwa_recipients: document.getElementById('cfg-wa-recipients').value,
        email_enabled: document.getElementById('cfg-email-enabled').checked,
        email_method: emailMethod(),
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
      if (channel === 'telegram' || channel === 'openwa') {
        // Como no desktop: o teste usa o que está digitado, sem exigir "Salvar" antes
        await fetch('/api/config', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            telegram_token: document.getElementById('cfg-tg-token').value, telegram_chat_ids: document.getElementById('cfg-tg-chats').value,
            openwa_url: document.getElementById('cfg-wa-url').value, openwa_api_key: document.getElementById('cfg-wa-key').value,
            openwa_recipients: document.getElementById('cfg-wa-recipients').value
          })
        });
      }
      if (channel === 'email') {
        await fetch('/api/config', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email_method: emailMethod(), email_recipients: document.getElementById('cfg-email-recipients').value })
        });
        if (emailMethod() === 'smtp') await saveSmtp();
      }
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
# Logo do app (assets/app-icon-original.png reduzida a 96 px) embutida: o Worker não serve arquivos estáticos
LOGO_B64 = "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"
LOGIN_HTML = LOGIN_HTML.replace("{{LOGO}}", LOGO_B64)
INDEX_HTML = INDEX_HTML.replace("{{LOGO}}", LOGO_B64)

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
    migrate_channel_flags(user_cfg())
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
        CTX["error"] = None
        tasks = await pol_get_tasks()
        events = await pol_get_events()
        classified = classify_items(tasks, events)
        return json_response(200, {**classified, "error": CTX.get("error")})

    # 2a. Dados completos para o Kanban e o calendário mensal
    if path == "/api/board":
        return json_response(200, board_data(await pol_get_tasks(), await pol_get_events()))

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

    # 4a. E-mail por senha de app do Google (SMTP)
    if path == "/api/smtp":
        sm = smtp_data()
        if method == "POST":
            try:
                req = json.loads(body_bytes.decode("utf-8"))
                for k in ("host", "user"):
                    if k in req:
                        sm[k] = str(req[k]).strip()
                if "port" in req:
                    sm["port"] = int(req["port"] or 465)
                if (req.get("password") or "").strip():
                    sm["password"] = "".join(req["password"].split())  # o Google mostra a senha em 4 blocos com espaços
                if req.get("clear"):
                    sm.pop("password", None)
            except Exception as e:
                return json_response(400, {"error": str(e)})
            save_store()
            return json_response(200, {"status": "ok"})
        return json_response(200, {"host": sm.get("host", "smtp.gmail.com"), "port": sm.get("port", 465),
                                   "user": sm.get("user", ""), "has_password": bool(sm.get("password"))})

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
                # Como no desktop: o teste manda o relatório real do momento, para conferir o layout que vai chegar
                classified = classify_items(await pol_get_tasks(), await pol_get_events())
                plain = format_telegram(classified).replace("*", "").replace("`", "")
                ok, msg = await send_email("[Teste] " + email_subject(classified), email_html(classified, user_first_name()), plain)
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

PT_WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]  # segunda = 0

def _hsl_hex(h: float, sat: float, l: float) -> str:
    c = (1 - abs(2 * l - 1)) * sat
    x = c * (1 - abs(h / 60 % 2 - 1))
    m = l - c / 2
    r, g, b = (c, x, 0) if h < 60 else (x, c, 0) if h < 120 else (0, c, x)
    return "#%02x%02x%02x" % (round((r + m) * 255), round((g + m) * 255), round((b + m) * 255))

def _contrast(hexs: str) -> float:
    def ch(i: int) -> float:
        v = int(hexs[i:i + 2], 16) / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 1.05 / (0.2126 * ch(1) + 0.7152 * ch(3) + 0.0722 * ch(5) + 0.05)

def urgency_color(days: Optional[float]) -> str:
    """Gradiente de urgência: vermelho (hoje) → laranja → amarelo → verde (7+ dias); atrasado vinho; sem prazo cinza.
    No e-mail (fundo branco) a luminosidade é reduzida até contraste >= 4,6:1."""
    if days is None:
        return "#57534e"
    if days < 0:
        return "#991b1b"
    hue = min(max(days, 0), 7) / 7 * 135
    l = 0.42
    while _contrast(_hsl_hex(hue, 0.85, l)) < 4.6 and l > 0.05:
        l -= 0.01
    return _hsl_hex(hue, 0.85, l)

def email_title() -> str:
    return f"Publicações Online - Relatório de Tarefas Diário para {datetime.now(TZ).strftime('%d/%m/%Y')}"

def email_subject(c: Dict[str, Any]) -> str:
    """Assunto = título; com prazo vencendo hoje ganha o prefixo URGENTE."""
    return ("URGENTE: " if c["today"] else "") + email_title()

def user_first_name() -> str:
    parts = (STORE["users"].get(CTX["sub"], {}).get("name") or "").split()
    return parts[0] if parts else ""

def _snippet(text: str, limit: int = 170) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].rstrip() + "…" if len(text) > limit else text

_INK, _MUTED, _RULE, _PAPER, _DESK = "#1c1917", "#57534e", "#e7e5e4", "#ffffff", "#f5f5f4"
_SERIF = "Georgia, 'Times New Roman', serif"
_SANS = "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

def email_html(c: Dict[str, Any], user_name: str = "") -> str:
    """E-mail do resumo diário / alerta urgente: mesmo layout do desktop (tabelas e estilos inline)."""
    esc = html.escape
    today = datetime.now(TZ).date()
    urgent = bool(c["today"])

    def row(it: Dict[str, Any]) -> str:
        dt = datetime.fromisoformat(it["deadline"]) if it.get("deadline") else None
        day = dt.strftime("%d/%m") if dt else "—"
        weekday = "sem prazo" if dt is None else PT_WEEKDAYS[dt.weekday()] if dt.year == today.year else str(dt.year)
        meta = "" if it["type"] == "Tarefa" else "Audiência / agenda"
        if dt and (dt.hour, dt.minute) < (23, 59) and not (dt.hour == 0 and dt.minute == 0):
            meta = (meta + " · " if meta else "") + dt.strftime("%H:%M")
        desc = _snippet(it["description"]) if it["description"] else ""
        tone = urgency_color(it.get("days"))
        meta_html = f'<div style="font-size:12px;line-height:16px;color:{tone};padding-top:2px;">{esc(meta)}</div>' if meta else ""
        desc_html = f'<div style="font-size:13px;line-height:19px;color:{_MUTED};padding-top:4px;">{esc(desc)}</div>' if desc else ""
        return f"""
<tr>
  <td valign="top" width="64" style="padding:14px 0;border-top:1px solid {_RULE};font-family:{_SANS};">
    <div style="font-size:15px;line-height:20px;font-weight:700;color:{tone};font-variant-numeric:tabular-nums;">{day}</div>
    <div style="font-size:12px;line-height:16px;color:{_MUTED};">{esc(weekday)}</div>
  </td>
  <td valign="top" style="padding:14px 0 14px 12px;border-top:1px solid {_RULE};font-family:{_SANS};word-break:break-word;overflow-wrap:anywhere;">
    <div style="font-size:14px;line-height:20px;font-weight:600;color:{tone};">{esc(it["title"].strip())}</div>
    {meta_html}
    {desc_html}
  </td>
</tr>"""

    def section(title: str, color: str, items: List[Dict[str, Any]], limit: Optional[int] = None, empty: Optional[str] = None) -> str:
        if not items and empty is None:
            return ""
        shown = items if limit is None else items[:limit]
        if items:
            rows = "".join(row(i) for i in shown)
        else:
            rows = f'<tr><td colspan="2" style="padding:14px 0;border-top:1px solid {_RULE};font-family:{_SANS};font-size:14px;line-height:20px;color:{_MUTED};">{empty}</td></tr>'
        more = ""
        if limit is not None and len(items) > limit:
            more = (f'<tr><td colspan="2" style="padding:12px 0 0;border-top:1px solid {_RULE};font-family:{_SANS};font-size:13px;color:{_MUTED};">'
                    f'E mais {len(items) - limit} no <a href="https://office.pol.adv.br/" style="color:{_INK};">POL ADV</a>.</td></tr>')
        return f"""
<tr><td style="padding:28px 32px 0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td style="padding-bottom:6px;font-family:{_SANS};font-size:13px;line-height:18px;font-weight:700;color:{color};white-space:nowrap;">{title}</td>
      <td align="right" style="padding-bottom:6px;font-family:{_SANS};font-size:13px;line-height:18px;color:{_MUTED};font-variant-numeric:tabular-nums;">{len(items)}</td>
    </tr>
  </table>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    {rows}{more}
  </table>
</td></tr>"""

    title = email_title()
    name = esc(user_name) if user_name.strip() else "Olá"
    intro = f"{name}, estas são suas tarefas e agenda para os próximos dias."
    n_today = len(c["today"])
    preview = f"{n_today} {'item vence' if n_today == 1 else 'itens vencem'} hoje." if n_today else "Nenhuma tarefa/agendamento para hoje."
    head_color = "#991b1b" if urgent else _INK
    band = (f'<tr><td style="background:#b91c1c;padding:10px 32px;font-family:{_SANS};font-size:12px;line-height:16px;font-weight:700;'
            f'letter-spacing:0.08em;color:#ffffff;text-transform:uppercase;">Atenção imediata</td></tr>') if urgent else ""
    sent = datetime.now(TZ).strftime("%d/%m/%Y às %H:%M")

    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>{esc(title)}</title></head>
<body style="margin:0;padding:0;background:{_DESK};">
  <div style="display:none;max-height:0;overflow:hidden;color:{_DESK};">{esc(preview)}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_DESK};">
    <tr><td align="center" style="padding:24px 12px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;background:{_PAPER};border-radius:12px;overflow:hidden;">
        {band}
        <tr><td style="padding:28px 32px 0;font-family:{_SERIF};font-size:24px;line-height:31px;font-weight:700;letter-spacing:-0.01em;color:{head_color};">{esc(title)}</td></tr>
        <tr><td style="padding:12px 32px 0;font-family:{_SANS};font-size:15px;line-height:22px;color:{_INK};">{intro}</td></tr>
        {section("Hoje", urgency_color(0), c["today"], empty="Nenhuma tarefa/agendamento para hoje.")}
        {section("Próximos 2 dias", urgency_color(1.5), c["tomorrow"])}
        {section("Ainda nesta semana", urgency_color(5), c["this_week"])}
        {section("Atrasados e sem prazo", urgency_color(-1), c["overdue"], limit=10)}
        <tr><td style="padding:32px 32px 0;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
            <td style="background:{_INK};border-radius:8px;">
              <a href="https://office.pol.adv.br/" style="display:inline-block;padding:12px 20px;font-family:{_SANS};font-size:14px;line-height:18px;font-weight:600;color:#ffffff;text-decoration:none;">Abrir o POL ADV</a>
            </td>
          </tr></table>
        </td></tr>
        <tr><td style="padding:28px 32px;font-family:{_SANS};font-size:12px;line-height:18px;color:{_MUTED};">
          Enviado pelo POL ADV Notifier em {sent}. Você recebe este resumo porque ativou as notificações por e-mail no app.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""

def _message_lines(c: Dict[str, Any], line, overdue_title: str, more_text: str, footer: str, title: str) -> str:
    out = [title + "\n"]
    for header, key in (("🚨 VENCENDO HOJE:", "today"), ("⚠️ AMANHÃ / PRÓXIMOS 2 DIAS:", "tomorrow"), ("📅 ESTA SEMANA:", "this_week")):
        if c[key]:
            out += [f"*{header}*"] + [line(i) for i in c[key]] + [""]
    if c["overdue"]:
        out.append(f"*{overdue_title}*")
        out += [line(i) for i in c["overdue"][:10]]  # limitar para não estourar a mensagem
        if len(c["overdue"]) > 10:
            out.append(f"_...e mais {len(c['overdue']) - 10} {more_text}_")
        out.append("")
    out.append(footer)
    return "\n".join(out)

def _agenda_tag(it: Dict[str, Any]) -> str:
    return "" if it["type"] == "Tarefa" else "*[Agenda]* "

def format_telegram(c: Dict[str, Any]) -> str:
    return _message_lines(c, lambda i: f"{urgency_emoji(i.get('days'))} `{i['deadline_str']}` — {_agenda_tag(i)}{i['title']}",
                          "⏳ ATRASADAS / PENDENTES:", "tarefas atrasadas", "🔗 [Acessar POL ADV](https://office.pol.adv.br)",
                          "⚖️ *POL ADV — Resumo de Prazos*")

def format_openwa(c: Dict[str, Any]) -> str:
    return _message_lines(c, lambda i: f"{urgency_emoji(i.get('days'))} *{i['deadline_str']}* — {_agenda_tag(i)}{i['title']}",
                          "⏳ TAREFAS ATRASADAS / PENDENTES:", "tarefas", "Acesse: https://office.pol.adv.br",
                          "*⚖️ POL ADV — Resumo de Prazos*")

async def send_report(classified: Dict[str, Any]) -> List[Tuple[str, bool, str]]:
    """Envia o resumo para todos os canais configurados do usuário em CTX (mesmas mensagens do desktop).
    Devolve (canal, ok, mensagem)."""
    cfg = user_cfg()
    results: List[Tuple[str, bool, str]] = []
    if cfg.get("telegram_enabled"):
        results.append(("Telegram", *await send_telegram(format_telegram(classified))))
    if cfg.get("openwa_enabled"):
        results.append(("WhatsApp", *await send_openwa(format_openwa(classified))))
    if cfg.get("email_enabled"):
        plain = format_telegram(classified).replace("*", "").replace("`", "")
        results.append(("E-mail", *await send_email(email_subject(classified), email_html(classified, user_first_name()), plain)))
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
        migrate_channel_flags(user["config"])
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
        global CF_ENV
        CF_ENV = self.env
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
        global CF_ENV
        CF_ENV = self.env
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
