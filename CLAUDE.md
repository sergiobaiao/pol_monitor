# CLAUDE.md

Guia para o Claude Code (claude.ai/code) neste repositório.

> **Regra de manutenção:** ao modificar qualquer coisa relevante (código, build, CI/CD, configuração, comportamento), atualize **`CLAUDE.md`, `AGENTS.md` e `GEMINI.md`** na mesma alteração. Os três descrevem o mesmo projeto para assistentes diferentes e não podem divergir. Mudanças de escopo também vão para `.spec-kit/spec.md` e `tasks.md`.

**POL ADV Notifier** (logo em `assets/app-icon-original.png`; `desktop-wpf/PolNotifier/app.ico` e `logo.png` e o `LOGO_B64` do `app-web.py` são derivados dela): monitora prazos/audiências da plataforma jurídica POL ADV (`office-api.pol.adv.br`) e notifica por E-mail, Telegram e WhatsApp. Detalhes de APIs, Keycloak, estrutura de arquivos e histórico de gotchas: [`AGENTS.md`](AGENTS.md). Repositório público: **https://github.com/sergiobaiao/pol_monitor** (nunca versionar segredos, `.env`, `data/` nem o JWT do POL).

---

## 🚀 Comandos

```powershell
# Desktop (.NET 10 WPF) — a edição ativa
.\desktop-wpf\build.ps1                       # compila (Debug; -Configuration Release)
dotnet run --project desktop-wpf\PolNotifier  # roda
.\desktop-wpf\publish.ps1 -Version 2.1.0      # .exe single-file self-contained + instalador NSIS (se houver makensis)

# Lançar versão: cria e envia a próxima tag vX.Y.Z (o CI publica a release e faz o deploy web)
.\release.ps1                                 # patch (2.0.0 -> 2.0.1); -Bump minor|major; -DryRun só mostra

# Web (Python)
python app-web.py                             # local, porta 8787/8080 (ver iniciar_web.bat)
python -c "import py_compile; py_compile.compile('app-web.py', doraise=True); print('OK!')"

# Swagger UI + Proxy CORS (porta 8000)
python server.py                              # ou .\iniciar_swagger.bat

# Legado (referência; NÃO rodar junto com o desktop .NET: duplica agendador e notificações)
python -m app.main                            # ou .\run_app.bat
```

Não há suíte de testes. Verificação: `dotnet build` sem warnings, `py_compile` do `app-web.py` e conferir o app rodando. O `nuget.config` de `desktop-wpf/` aponta para o nuget.org (o NuGet global da máquina está sem fontes). SDKs .NET 10.0.303/10.0.403.

---

## 🏛️ Arquitetura

### Três componentes
- **`desktop-wpf/`** — Desktop em **.NET 10 + WPF** (substitui `app/`). Ver abaixo.
- **`app-web.py`** — Arquivo único e **autocontido** (SPA embutida + lógica própria de Keycloak/classificação). **Não importa nada de `desktop-wpf/` nem de `app/`**: regra de negócio corrigida num lado (classificação, formatação, gradiente de urgência) precisa ser replicada no outro. Detecta Cloudflare via `IS_CLOUDFLARE`.
- **`server.py` + `index.html` + `swagger/`** — Explorador OpenAPI do POL com proxy CORS. `swagger/*.json` é gerado por `download_specs.py` com `POL_TOKEN` (não versionado).
- **`app/`** — Desktop antigo em Python/CustomTkinter. Fica só como referência até o .NET ser aprovado; não evoluir.

### Desktop .NET (`desktop-wpf/PolNotifier/`)
- **Fluxo (`App.xaml.cs`):** mutex `Local\POL_ADV_Notifier_Desktop` (instância única; abrir de novo sinaliza o evento `...Show` e a instância aberta mostra o painel) → `Login` (auto com senha lembrada, senão `LoginWindow`) → `Session` (banco do usuário, `Config`, `PolClient`, `Scheduler`, `FenceWindow`, `MainWindow`, `NotifyIcon`). "Trocar usuário" descarta a `Session` e volta ao login sem auto-login. Eventos do `Scheduler` vêm de outra thread: use `Dispatcher.BeginInvoke`.
- **`Core/Database.cs`** — SQLite no **mesmo esquema** do Python (`settings`, `cached_items`, `notification_history`, `app_logs`). Pasta `data/`: `POL_DATA_DIR` → sobe do `.exe` até achar `data\pol_notifier.db` → `%LOCALAPPDATA%\POL ADV Notifier\data` (app instalado). `data/users/<sub>.db` por usuário; `data/pol_notifier.db` global só com `remembered_email`/`migrated_to`. `OpenUser` migra o banco legado na 1ª vez.
- **`Core/Config.cs`** — toda configuração é propriedade (chaves/padrões iguais a `app/config.py`). `Config.StartWithWindows` é estática e grava `HKCU\...\Run` (mesmo valor do instalador).
- **`Core/CredentialStore.cs`** — Credential Manager no formato do `keyring` do Python: genérica, alvo `POL ADV Notifier`, usuário = e-mail, blob UTF-16; se outro e-mail já ocupa o alvo, usa `e-mail@POL ADV Notifier`. Senha e tokens **nunca** vão para o SQLite.
- **Conta da API ≠ login** (`Core/PolClient.cs`): o login do app identifica o usuário e o banco (`data/users/<sub>.db`); a **conta da API** é quem de fato é consultado no POL (token e `x-selecteduserid` = `sub` dela). Por padrão é a do login; em Configurações → Conta POL ADV o usuário informa outra (`SetApiAccount`: valida no Keycloak antes de salvar; `UseLoginAccount` volta ao login). Usuário em `pol_api_user`; **senha no Credential Manager, item separado** (serviço `POL ADV Notifier API`). Ao trocar de conta o cache (`cached_items`) é limpo e o escritório é re-detectado. Na abertura, `ApplySavedApiAccount` entra com a conta salva (se falhar, cai no login e registra no log).
- **`Core/PolClient.cs`** — Keycloak (`web_app`, `grant_type=password`), refresh, relogin com a senha em memória. **O gateway responde 403 sem `User-Agent`** (o `HttpClient` não envia): o cliente define um. Busca paginada (`size=100&page=N`) de `task/tasks?active=true` e `calendar/events`.
- **`Core/Formatter.cs`** — classifica em Hoje / +2 dias / semana / atrasadas **no horário local** (a API manda UTC: `02:59:59Z` = 23:59 em Brasília; o Python errava o dia). Prazos "fim do dia" (23:59) exibem só a data. Gera o HTML do e-mail (tabelas + estilos inline; título/assunto `Publicações Online - Relatório de Tarefas Diário para dd/MM/yyyy`, prefixo `URGENTE:` se há prazo hoje; seção "Hoje" sempre primeiro, com "Nenhuma tarefa/agendamento para hoje."), o texto do Telegram/WhatsApp e o **gradiente de urgência** (`UrgencyColor`: vermelho hoje → laranja → amarelo → verde ≥7 dias; atrasado vinho; sem prazo cinza; `dark:true` para fundo escuro, senão escurece até contraste ≥ 4,5:1 no e-mail; `UrgencyEmoji` 🔴🟠🟡🟢⚪ para Telegram/WhatsApp).
- **`Core/Notifiers.cs`** — E-mail (MailKit, vários destinatários separados por `,`/`;`), Telegram, OpenWA. Envio parcial vira aviso (⚠️), com dica para "chat not found" (a pessoa precisa dar `/start` no bot).
- **`Core/Scheduler.cs`** — thread própria; `SyncAndNotify` (com `SemaphoreSlim`), `SendReport` (deduplicação diária por canal; `force` ignora) e `SendLastReport` (botão "Enviar último relatório"). Mudou o intervalo? Chame `RestartCycle()`.
- **`Core/Board.cs`** — modelo do Kanban/calendário como no POL (`BoardItem`, colunas `TO_DO/IN_PROGRESS/REVIEW/DONE`).
- **`Ui/FenceWindow.*`** — widget estilo Fences: **owner = `Progman`** (`SetWindowLongPtr(GWLP_HWNDPARENT)`), `WS_EX_TOOLWINDOW|WS_EX_NOACTIVATE`, `HWND_BOTTOM`; timer de 1 s refaz owner/z-order. **Não use `SetParent`** (Progman tem `WS_EX_NOREDIRECTIONBITMAP` no Win 11 24H2+: filhos de outro processo não são desenhados) e **nunca restaure o widget por conta própria** (`Show`/`WindowState=Normal` em resposta a minimizar/ocultar quebra o 2º Win + D). Posição em pixels físicos (como o Tk gravava), sem clamp por tela (multi-monitor). Recolhível; posição/tamanho/opacidade/recolhido salvos por usuário.
- **`Ui/MainWindow.*`** — abas Início, Tarefas (Lista = padrão, Kanban com as 4 colunas do POL ordenadas pelo `score` crescente), Calendário (navegação de mês compartilhada; Mês = grade como no POL com cores pendente/atrasada/concluída, entradas de 2 linhas e rolagem até a semana de hoje; Lista = tudo do mês por dia), Notificações, Configurações, Logs. **Clicar** numa entrada do mês, num item da lista ou num cartão do Kanban abre o popup de detalhes (`Alert.Detail`); "+N mais" abre a lista do dia. Os botões "Testar" **salvam os campos antes de enviar**. Fechar/minimizar vão para a bandeja conforme `close_to_tray`/`minimize_to_tray`.
- **Estilo:** dark mode Fluent (`#09090b`, `#18181b`, `#202024`, `#27272a`); cor de destaque do Windows em `Ui/Theme.cs`. Diálogos informativos = `Ui/Alert.cs` (nunca um modal que peça texto sem necessidade). Código, logs e UI em português.

### Web (`app-web.py`)
- Toda E/S de rede é `async` (`http_request`: `urllib` local, `fetch` do SDK `workers` no Cloudflare). Entrypoint: `class Default(WorkerEntrypoint)` com `fetch`/`scheduled`. Monitor 24h = `run_cron()` (cron `*/5` no Worker; cada usuário respeita o próprio intervalo; só avisa se há item não avisado hoje).
- **Conta da API na web** (`/api/api-account`, `activate_api_auth`): `users[sub].api = {user, sub, refresh_token}` quando for diferente do login; **a senha nunca é guardada** (só o refresh token, como no "Lembrar"). Os dados do POL usam `CTX["data_auth"]` (conta da API) em vez de `CTX["auth"]` (login), inclusive no cron; `CTX["data_auth"]` é zerado no início de **cada** requisição para nunca vazar entre usuários. Teste: simular Keycloak/gateway e conferir `x-selecteduserid`/`x-officeid`.
- **Persistência = KV `POL_STORE`** (declarado em `wrangler.toml` **sem `id`**: o wrangler provisiona sozinho no deploy). Sem o KV cada requisição começa vazia e "Lembrar" some / o cron não tem usuários. Estado: `STORE.users[sub]` (config, refresh token só de quem marcou "Lembrar", gmail), `STORE.sessions`. Cookie `pol_sid` (`HttpOnly`, `SameSite=Lax`).
- **E-mail na web, dois métodos** (`email_method` na config do usuário): **OAuth/Gmail API** (cliente OAuth "Aplicativo da Web" no Google Cloud, em produção: em "Teste" o token expira em 7 dias) ou **senha de app do Google via SMTP** (`/api/smtp`; credenciais em `STORE.users[sub].smtp`, a senha nunca volta ao navegador). O SMTP usa `smtplib` local. No Worker o **Python ainda não faz TLS em sockets** ("SSL/TLS via create_connection is not yet supported", confirmado em produção), então `smtp_send_rpc` chama por **RPC (service binding `SMTP`, payload em texto JSON, não objeto)** o Worker JavaScript auxiliar **`pol-adv-smtp`** (`smtp-worker/`: `smtp.js` cliente puro, `index.js` entrypoint, `test.mjs` testa em Node contra servidor falso), que usa `connect()` de `cloudflare:sockets` com TLS (465) ou STARTTLS (587). A porta 25 é bloqueada pela Cloudflare. "Testar E-mail" salva método, destinatários e credenciais antes de enviar.
- **Tarefas/Calendário na web** (iguais ao desktop e ao POL): `GET /api/board` devolve todas as tarefas (inclusive concluídas, com `status`, `priority`, `category`, `assignee`, `score`) e os compromissos; a busca no POL é paginada (`pol_fetch_all`, `active=true`). Tarefas = Lista (padrão) + Kanban (4 colunas, ordem por `score`); Calendário = Mês + Lista; clique abre o popup (`showDetail`/`showDay`). O JS monta tudo com `textContent` (dados do POL nunca entram como HTML).
- **Mensagens iguais às do desktop** (e-mail, Telegram, WhatsApp, assunto): o formatador do `Formatter.cs` está espelhado em `app-web.py` (`email_html`, `email_subject`, `format_telegram`, `format_openwa`, `urgency_color`, `urgency_emoji`, `fmt_deadline`, `classify_items`). **Mudou um, mude o outro.** Conferido por comparação byte a byte (HTML normalizado) sobre os mesmos dados reais: gere os textos com o C# (`Formatter.ToHtml/ToTelegramMarkdown/ToOpenWaText/EmailSubject`) e com o Python e compare. "Testar E-mail" da web, como o do desktop, envia o relatório real (assunto `[Teste] ...`).
- **Interruptores "Ativo" por canal** (e-mail, Telegram, WhatsApp) como no desktop: `email_enabled`, `telegram_enabled`, `openwa_enabled`; `send_report`/cron só enviam aos ativos (os botões "Testar" ignoram o interruptor e salvam os campos antes). `migrate_channel_flags` liga uma única vez Telegram/WhatsApp para quem já tinha os dados (antes enviavam sempre que configurados).
- `POST /api/send-report` (botão "Enviar relatório agora" em Configurações) sincroniza e envia para todos os canais; `send_report()` é compartilhada com o cron. `urgency_emoji()` espelha o `UrgencyEmoji` do desktop.
- Datas no fuso de Brasília (`TZ`). O `http_request` do Worker envia `User-Agent` (o gateway dá 403 sem ele); erros de sessão/busca voltam em `/api/sync` (`error`) e aparecem no status da tela.

---

## 📦 CI/CD (GitHub Actions, `.github/workflows/`)
- **`desktop.yml`** (windows-latest): build + `publish.ps1` (instala o NSIS por choco) em push/PR que toque `desktop-wpf/**`; em tag `v*` cria a **release** (marcada *latest*) com `PolNotifier-Setup-X.Y.Z.exe`, `PolNotifier-Setup.exe` (nome fixo: `releases/latest/download/PolNotifier-Setup.exe`) e `PolNotifier.exe`.
- **`web.yml`** (ubuntu): `py_compile`, `node smtp-worker/test.mjs`, depois `wrangler deploy` do Worker auxiliar (`smtp-worker/`) **e em seguida** do principal (ele o referencia por service binding), em push que toque `app-web.py`/`wrangler.toml`/`smtp-worker/**` e em tags. Segredos do repositório: `CLOUDFLARE_API_TOKEN` e `CLOUDFLARE_ACCOUNT_ID` (o workflow descarta CRLF/BOM invisíveis: segredo gravado por pipe do PowerShell 5.1 vem sujo). As chaves do R2 do `.env` não são usadas.
- **Instalador (`desktop-wpf/installer/PolNotifier.nsi`):** por usuário, sem administrador, em `%LOCALAPPDATA%\Programs\POL ADV Notifier`; opções: atalho na Área de Trabalho, pasta no Menu Iniciar, iniciar com o Windows. Fecha a versão aberta ao atualizar; desinstalar mantém dados e senha lembrada. `.nsi` com BOM UTF-8.
- O limite da API do GitHub é 5.000/h por usuário: ao acompanhar runs com `gh`, espere o reset em vez de repetir em loop.

---

## ⚠️ Armadilhas conhecidas
- Rodar o .NET e o Python ao mesmo tempo duplica agendador/notificações.
- PowerShell 5.1: scripts `.ps1` sem BOM são lidos como ANSI (acentos quebram); funções com o mesmo nome de um executável recursam (`Git` → use `Invoke-Git`).
- Heredocs no bash com aspas/`\n` quebram strings de código: escreva o trecho num arquivo e aplique com Python.
- Pasta `data/` e `.env` ficam fora do git; `swagger/*.json` também.
