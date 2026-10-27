# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**POL ADV Notifier**: monitora prazos/audiências da plataforma jurídica POL ADV (`office-api.pol.adv.br`) e notifica por E-mail, Telegram e WhatsApp. Detalhes de APIs, Keycloak e histórico de gotchas: [`AGENTS.md`](AGENTS.md). Requisitos e decisões (Spec-Kit/SDD): [`.spec-kit/`](.spec-kit/) — mudanças de escopo devem ser refletidas em `spec.md`/`tasks.md`.

---

## 🚀 Comandos

```powershell
# Desktop Notifier (Tray + Widget Fences)
python -m app.main            # ou .\run_app.bat

# Versão Web local (HTTP na porta 8080)
python app-web.py             # ou .\iniciar_web.bat

# Swagger UI + Proxy CORS (porta 8000)
python server.py              # ou .\iniciar_swagger.bat

# Deploy no Cloudflare Workers (cron seg-sex 08/13/18 UTC, ver wrangler.toml)
npx wrangler deploy

# Checagem de compilação (não há suíte de testes no projeto)
python -c "import py_compile; [py_compile.compile(f, doraise=True) for f in ['app/main.py','app/ui/desktop_fence.py','app/ui/main_window.py','app/scheduler.py','app-web.py']]; print('OK!')"
```

Dependências: `requirements.txt` **não** lista `customtkinter` nem `pywin32` (`keyring` sim), mas o app desktop importa `customtkinter`. Instale-o manualmente (`pip install customtkinter`). Runtime alvo: Python 3.14 no Windows.

---

## 🏛️ Arquitetura

### Três componentes independentes
- **`app/`** — Desktop app (CustomTkinter + pystray). Fluxo: `main.py` monta `Database → Config → PolClient → Scheduler → DesktopFenceWidget (raiz) → MainWindow (Toplevel) → TrayIconManager`.
- **`app-web.py`** — Arquivo único e **autocontido** (SPA embutida + lógica própria de Keycloak/classificação). **Não importa nada de `app/`**: correções de regra de negócio em `app/` (ex.: `formatter.py`, `pol_client.py`) precisam ser replicadas manualmente aqui. Detecta Cloudflare via `from js import Response` (`IS_CLOUDFLARE`).
- **`server.py` + `index.html` + `swagger/`** — Explorador OpenAPI das rotas do POL com proxy CORS para `office-api.pol.adv.br`.

### Login e multiusuário
- **Desktop:** `main.py` chama `run_login()` (`app/ui/login_window.py`, `ctk.CTk` própria, destruída antes do Fence) e só então `run_app()` monta tudo com `open_user_db(sub, email)` → `data/users/<sub>.db`. "Trocar Usuário" (bandeja/Configurações) destrói o Fence e volta ao login; o loop em `main()` remonta Scheduler/Fence/MainWindow com o banco do novo usuário. `data/pol_notifier.db` é o banco global: só `remembered_email`/`migrated_to` (o banco legado é migrado na 1ª vez para o dono, por `pol_user_id`/`pol_email`).
- Senha "lembrada" fica no Windows Credential Manager (`keyring`, serviço `POL ADV Notifier`); senha e tokens nunca vão para o SQLite (`open_user_db` apaga `pol_password`/tokens legados). `PolClient` guarda a senha só em memória para relogar quando o refresh expira.
- **Web (async):** toda E/S de rede é `async` (`http_request` usa `urllib` local e `fetch` do SDK `workers` no Cloudflare; urllib não funciona no Pyodide). Entrypoint do Worker: `class Default(WorkerEntrypoint)` com `fetch`/`scheduled`; flag `disable_python_external_sdk` no `wrangler.toml`. Monitor 24h = `run_cron()`: cron `*/5` no Worker e thread `Monitor24h` no servidor local (lock global com as requisições); cada usuário lembrado é sincronizado no seu `check_interval_minutes`, e o resumo só sai se houver item ainda não avisado hoje (`user["notified"]`). Datas no fuso de Brasília (`TZ`). Testar o Worker local: `npx wrangler dev` e `curl "http://127.0.0.1:8787/cdn-cgi/handler/scheduled?cron=*/5+*+*+*+*"`.
- **E-mail na web = Gmail API via OAuth do próprio usuário** (o Worker não tem SMTP): cada usuário cria um cliente OAuth "Aplicativo da Web" no Google Cloud (passo a passo na aba Notificações), salva em `/api/gmail` e conecta em `/oauth/google/start` → `/oauth/google/callback` (`state` anti-CSRF; escopos `openid email gmail.send`). Fica em `STORE.users[sub].gmail` (`client_id`, `client_secret`, `refresh_token`, `email`); `invalid_grant` desconecta. O app OAuth precisa estar "Em produção" (em "Teste" o Google expira o token em 7 dias). Por isso o cookie é `SameSite=Lax`.
- **Web:** cookie `pol_sid` (`HttpOnly`, `SameSite=Lax`); `STORE` (`data/web_store.json` local / KV `POL_STORE` no Cloudflare) guarda config por `sub`, sessões lembradas e refresh token só de quem marcou "Lembrar". `CTX` aponta o usuário da requisição/cron. `on_scheduled` → `run_cron()` notifica cada usuário com refresh token.
- Não há mais JWT hardcoded; `server.py` usa `POL_TOKEN` opcional do ambiente.

### Pipeline de sincronização (`app/scheduler.py`)
`PolClient.get_tasks()/get_events()` → `NotificationFormatter.classify_items()` (seções `today`, `tomorrow`, `this_week`, `overdue`) → para cada canal habilitado, envia um resumo **consolidado** se algum item ainda não foi notificado hoje naquele canal (`db.is_already_notified(id, CHANNEL, "CONSOLIDATED", date)`); `force=True` ignora a deduplicação. Depois chama `on_sync_complete`, que `main.py` encadeia para atualizar o Fence.

### Hierarquia de janelas (Tkinter / Win32)
- **`DesktopFenceWidget` (`app/ui/desktop_fence.py`) é a raiz (`ctk.CTk`)**: janela top-level `WS_EX_TOOLWINDOW` cujo **owner** é o `Progman` (`SetWindowLongPtrW(GWLP_HWNDPARENT)`, em `set_owner_async`), mantida em `HWND_BOTTOM`. Assim sobrevive ao `Win + D` e fica atrás dos apps. Não restaure o widget por conta própria (`deiconify` em `<Unmap>` ou em loop): o Windows trata isso como janela restaurada e o segundo `Win + D` deixa de devolver as janelas abertas. **Não use `SetParent`** para o `Progman`/`WorkerW`: no Windows 11 24H2+ o `Progman` tem `WS_EX_NOREDIRECTIONBITMAP` e filhos de outro processo não são compostos (invisíveis com `-alpha`, lixo sem). O Stardock Fences só faz isso porque roda dentro do explorer.exe (`DesktopDock64.dll` injetada). Chamadas Win32 que mandam mensagens síncronas ao WndProc do Tk (`SetParent`, `SetWindowLongPtr`...) devem ir para outra thread: feitas na thread do Tk via ctypes derrubam o Python 3.14 com `PyEval_RestoreThread`. Nunca fechar/destruir a raiz para esconder o widget; ela roda o `mainloop()`.
- **`MainWindow` (`app/ui/main_window.py`) é `ctk.CTkToplevel`** com o fence como master; esconde com `withdraw()` / restaura com `deiconify()` via tray, sem afetar o widget.
- Não clampar a posição do widget por `winfo_screenwidth()` (quebra multi-monitor); a geometria salva no SQLite é usada exatamente.

### Threading
- `Scheduler` roda em thread daemon. Alterou o intervalo? Chame `scheduler.restart_cycle()` (dispara `_wake_event`).
- Qualquer atualização de UI vinda do scheduler ou de requisições em background deve ir para a thread do Tk com `fence.after(0, cb)` / `app.after(0, cb)`.

### Persistência
- SQLite em `data/pol_notifier.db` (WAL). Tabelas: `settings` (chave-valor), `cached_items`, `notification_history` (deduplicação), `app_logs`. Use `db.log(level, msg)` para logs exibidos na aba Logs.
- Toda nova configuração vira `@property` (getter + setter) em [`app/config.py`](app/config.py), persistida em `settings`. Padrões: `desktop_fence_enabled=true`, `start_minimized=true`, `minimize_to_tray=true`, `close_to_tray=true`, `check_interval_minutes=60` (mín. 1).

### Estilo de UI
- Dark mode Fluent (`#18181b`, `#202024`, `#27272a`); cor de destaque do Windows via `get_windows_accent_color()` em [`app/ui/theme.py`](app/ui/theme.py).
- Diálogos informativos usam o modal `ModernAlert`; **nunca** `CTkInputDialog` quando não há entrada de texto.
- Código, comentários, logs e textos da UI em português.
