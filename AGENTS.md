# AGENTS.md — Guia de Arquitetura e Continuidade (POL ADV Notifier)

Este documento guia qualquer agente de IA ou desenvolvedor na continuidade, manutenção e evolução deste projeto.

> **Regra de manutenção:** ao modificar qualquer coisa relevante (código, build, CI/CD, configuração, comportamento), atualize **`CLAUDE.md`, `AGENTS.md` e `GEMINI.md`** na mesma alteração; os três descrevem o mesmo projeto para assistentes diferentes. Este arquivo é a referência detalhada (APIs, estrutura, gotchas); `CLAUDE.md` e `GEMINI.md` são os guias de trabalho.

Repositório público: https://github.com/sergiobaiao/pol_monitor. Nunca versionar `.env`, `data/`, `swagger/*.json` (gerado com o JWT do usuário) nem tokens.

---

## 1. Visão Geral do Projeto

O **POL ADV Notifier** é um ecossistema completo para monitoramento automatizado, visualização e alertas de prazos processuais e audiências da plataforma jurídica **POL ADV** (`https://office.pol.adv.br/`).

O projeto é composto por:
1. **Desktop App (Windows) — `desktop-wpf/`, .NET 10 + WPF**: tema escuro Fluent, bandeja (`NotifyIcon`), agendador em background, notificações multicanal (E-mail, Telegram, WhatsApp), painel com Kanban e calendário mensal como no POL e **Widget de Área de Trabalho estilo Stardock Fences**. Substitui a versão Python (`app/`), mantida só como referência.
2. **Swagger UI & Proxy Local**: Servidor local em `http://127.0.0.1:8000` (`server.py`) com especificações OpenAPI 3.0 consolidadas das 476 rotas da API do POL ADV e proxy CORS reverso.
3. **Web Edition & Cloudflare Workers**: Aplicação web (`app-web.py`) com SPA embutida, execução local ou Python Worker (Pyodide) em `https://pol-adv-web.victorlimapge.workers.dev`, com persistência no KV `POL_STORE` e e-mail por OAuth (Gmail API) **ou** senha de app do Google (SMTP; no Worker via RPC para o Worker JavaScript auxiliar `pol-adv-smtp`, em `smtp-worker/`, porque o Python Worker ainda não faz TLS em sockets). Independente do desktop: regra de negócio corrigida num lado deve ser replicada no outro. A web também tem Tarefas (Lista/Kanban) e Calendário (Mês/Lista) com popup de detalhes, alimentados por `GET /api/board`.

**Paridade das mensagens:** o formatador (e-mail HTML, Telegram, WhatsApp, assunto, gradiente de urgência, prazo "fim do dia") existe em C# (`Formatter.cs`) e em Python (`app-web.py`: `email_html`, `format_telegram`, `format_openwa`, `email_subject`, `urgency_color`, `fmt_deadline`) e precisa produzir texto idêntico. Ao mexer num, replique no outro e compare as saídas sobre os mesmos dados (só diferem na codificação de entidades HTML e no horário "Enviado em").

**Identidade visual:** logo em `assets/app-icon-original.png` (fundo transparente). Derivados: `desktop-wpf/PolNotifier/app.ico` (exe, janelas, bandeja, instalador NSIS), `desktop-wpf/PolNotifier/logo.png` (telas do desktop) e `LOGO_B64` em `app-web.py` (96 px, favicon e cabeçalho). Para trocar a logo, regenerar os três a partir do original.
4. **CI/CD e releases** (`.github/workflows/`, `release.ps1`, instalador NSIS): ver seção 7.

---

## 2. Topologia de APIs e Autenticação (POL ADV)

- **Portal Web:** `https://office.pol.adv.br/`
- **API Gateway:** `https://office-api.pol.adv.br`
- **Servidor Keycloak (OAuth2 Direct Grant):** `https://keycloak.pol.adv.br/auth/realms/pol-prod`
  - Token Endpoint: `/protocol/openid-connect/token`
  - Client ID: `web_app` (Direct Grant: `grant_type=password`, `username`, `password`)
  - Auto-refresh de tokens via `refresh_token` em [`desktop-wpf/PolNotifier/Core/PolClient.cs`](desktop-wpf/PolNotifier/Core/PolClient.cs) (legado: `app/pol_client.py`).
- **Tenant ID (`x-officeid`):** `25324` e `x-selecteduserid` = `sub` do JWT (lidos das claims no login; o primeiro escritório de `office/offices` é usado se não houver `pol_office_id`).
- **Usuário Padrão:** Sérgio Baião (`sergiobaiao@gmail.com`), UUID: `46e0695a-490e-49f4-b660-d19af55d956d`.
- **O gateway (nginx) responde `403` a requisições sem `User-Agent`.** O `HttpClient` do .NET não envia um por padrão: o cliente define `PolNotifier/2.0`.
- **Rotas Principais da API** (paginar com `size=100&page=N`; o total vem em `X-Total-Count`):
  - Tarefas: `GET /services/lawoffice/api/task/tasks?active=true`. Campos: `id`, `name`, `description`, `status` (`TO_DO`, `IN_PROGRESS`, `REVIEW`, `DONE`), `priority` (`HIGH`/`MEDIUM`/`LOW`), `deadline`, `category` {`name`,`color`}, `assignedTo` {`name`}, `isArchived`. O Kanban do POL (`/tasks`) usa as 4 colunas de `status`.
  - Compromissos / Audiências: `GET /services/lawoffice/api/calendar/events` (`startDate`, `endDate`, `title`, `category`, `calendar`). `GET /calendar/calendars` lista as agendas ("Escritório", "Pessoal").
  - **Datas em UTC.** Prazos de tarefa vêm como `…T02:59:59Z` = 23:59:59 em Brasília ("fim do dia"): converter para o fuso local antes de classificar/exibir.

---

## 3. Desktop .NET (`desktop-wpf/`) — edição ativa

Projeto `desktop-wpf/PolNotifier` (net10.0-windows, WPF + WinForms só pelo `NotifyIcon`; pacotes `Microsoft.Data.Sqlite` e `MailKit`). Compilar com `desktop-wpf\build.ps1`; publicar com `publish.ps1`.

| Arquivo | Papel |
|---|---|
| `App.xaml.cs` | Mutex de instância única (+ evento para mostrar o painel), login, `Session` (banco, agendador, widget, painel, bandeja), "Trocar usuário" |
| `Core/Database.cs`, `Core/Config.cs` | SQLite no mesmo esquema do Python; `data/users/<sub>.db`; resolução da pasta `data/` (`POL_DATA_DIR` → acima do .exe → `%LOCALAPPDATA%\POL ADV Notifier\data`); `Config.StartWithWindows` (HKCU Run) |
| `Core/CredentialStore.cs` | Credential Manager no formato do `keyring` (alvo `POL ADV Notifier`, usuário = e-mail, UTF-16; alvo composto `e-mail@POL ADV Notifier`) |
| `Core/PolClient.cs` | Keycloak, refresh, relogin, busca paginada |
| `Core/Formatter.cs` | Classificação no horário local, e-mail HTML, texto Telegram/OpenWA, gradiente de urgência (`UrgencyColor`, `UrgencyEmoji`) |
| `Core/Notifiers.cs`, `Core/Scheduler.cs` | Envio (MailKit/Telegram/OpenWA), ciclo, deduplicação diária por canal, `SendLastReport` |
| `Core/Board.cs` | Modelo do Kanban e do calendário mensal |
| `Ui/FenceWindow.*` | Widget de Área de Trabalho (regras na seção 3b/6) |
| `Ui/MainWindow.*`, `Ui/LoginWindow.*`, `Ui/Alert.cs`, `Ui/Theme.cs` | Painel, login, modal informativo, cor de destaque do Windows |
| `installer/PolNotifier.nsi` | Instalador NSIS por usuário |

Comportamentos que não são óbvios:
- **E-mail:** título e assunto `Publicações Online - Relatório de Tarefas Diário para dd/MM/yyyy`; com prazo vencendo hoje o assunto ganha `URGENTE:` e o corpo uma faixa vermelha. Saudação `"<primeiro nome>, estas são suas tarefas e agenda para os próximos dias."`; a seção "Hoje" vem sempre primeiro (vazia = "Nenhuma tarefa/agendamento para hoje."). Tabelas e estilos inline (Gmail/Outlook ignoram `<style>`/flex). Títulos e datas coloridos pelo gradiente de urgência; no e-mail a luminosidade é reduzida até contraste ≥ 4,5:1 sobre branco.
- **Telegram/WhatsApp** não têm cor de texto: o gradiente vira um emoji por item (⛔ atrasado, 🔴 hoje, 🟠 ≤2 dias, 🟡 ≤5, 🟢 mais longe, ⚪ sem prazo).
- **Prazos de "fim do dia"** (23:59 local) são exibidos só com a data; eventos com hora real mostram a hora.
- **Botões "Testar"** (e-mail/Telegram/WhatsApp) salvam os campos digitados antes de enviar. **"Enviar último relatório"** (Configurações) reenvia para os canais ativos ignorando a deduplicação.
- **Início com o Windows:** checkbox em Configurações → Inicialização; mesmo valor `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\POL ADV Notifier` que o instalador grava.
- **Painel:** Tarefas (Lista = padrão, Kanban como em `office.pol.adv.br/tasks`: colunas por `status`, cartões ordenados pelo campo `score` crescente como o POL) e Calendário (Mês como em `office.pol.adv.br/calendar`, Lista com tudo do mês por dia). Clique numa entrada/cartão abre o popup de detalhes (`Alert.Detail`); "+N mais" abre o dia. Cores: pendente azul, atrasada vinho, concluída verde (tarefas); compromissos usam a cor da categoria.

## 3b. Widget de Área de Trabalho (regras valem para .NET e Python)

No .NET: `FenceWindow` é uma janela top-level `WS_EX_TOOLWINDOW|WS_EX_NOACTIVATE` com **owner = `Progman`** (`SetWindowLongPtr(GWLP_HWNDPARENT)`) mantida em `HWND_BOTTOM`; um timer de 1 s refaz o owner (Explorer reiniciado) e reaplica `HWND_BOTTOM` (`SWP_NOACTIVATE|SWP_NOMOVE|SWP_NOSIZE`). Posição em pixels físicos, sem clamp por tela. **Nunca** `SetParent` no Progman/WorkerW e **nunca** restaurar o widget por conta própria (`Show`/`WindowState=Normal`). Validado com o teste Win + D duas vezes (janela volta, widget segue visível). Sem a restrição de thread do Python (o crash `PyEval_RestoreThread` não existe no .NET).

## 3c. Desktop legado (Python/Tkinter) — apenas referência

### 3c.1 Inversão Arquitetural de Janelas (Tkinter & Win32)
Para alcançar o comportamento exato do **Stardock Fences** (imunidade a `Win + D`, independência de visibilidade em relação ao painel principal e ausência de travamentos no Python):

- **Janela Raiz (`ctk.CTk`):** É o [`DesktopFenceWidget`](file:///z:/pol/app/ui/desktop_fence.py).
  - Como janela raiz, não possui master no Tkinter.
  - **Owner = `Progman`** (`SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, progman)`), continuando top-level. Não é reparentada: no Windows 11 24H2+ o `Progman` tem `WS_EX_NOREDIRECTIONBITMAP` e janelas filhas de outro processo não são desenhadas.
  - **Crítico para Python 3.14:** As chamadas Win32 de fixação rodam em outra thread (`set_owner_async`). Chamadas na thread do Tk (com `WinDLL` ou `PyDLL`), o Windows despacha mensagens sincronicamente ao `WndProc` do Tkinter, que executa callbacks Python fora do contexto Tcl e causa o erro fatal `PyEval_RestoreThread`. De outra thread, as mensagens são entregues pelo loop de eventos do Tcl.
  - Estilo estendido `WS_EX_TOOLWINDOW` para nunca aparecer no `Alt + Tab` nem na barra de tarefas.
  - Camada de fundo permanente `HWND_BOTTOM`.
  - Pressionar **`Win + D`** não minimiza o widget, pois janelas cujo owner é o `Progman` são tratadas como parte do desktop.
  - Posição exata (`x, y`), dimensões (`w, h`) e opacidade (`alpha`) são salvas continuamente no banco SQLite.
  - Alça visual de redimensionamento (`⤡`) no canto inferior direito.
- **Painel de Controle (`ctk.CTkToplevel`):** É a [`MainWindow`](file:///z:/pol/app/ui/main_window.py).
  - Instanciada tendo o widget raiz como mestre.
  - Quando fechada (`X`) ou minimizada (`_`), executa `self.withdraw()`, ocultando-se para o tray icon **sem afetar em nada o widget do desktop**, que permanece visível.
  - Restauração com 1 clique no ícone da bandeja (`pystray`).
- **Sincronização com Tema do Windows:**
  - [`app/ui/theme.py`](file:///z:/pol/app/ui/theme.py) lê diretamente do Registro do Windows (`HKEY_CURRENT_USER\Software\Microsoft\Windows\DWM` -> `AccentColor`) a cor de destaque ativa do usuário, aplicando-a dinamicamente a botões, cartões e cabeçalhos.

### 3c.2 Persistência e Banco de Dados Local
- **Arquivo:** `z:\pol\data\pol_notifier.db` (SQLite com `WAL mode`).
- **Tabelas (`app/database.py`):**
  - `settings`: pares chave-valor para credenciais, preferências e geometria do widget.
  - `cached_items`: cópia local de tarefas e audiências para inicialização instantânea offline.
  - `notification_history`: controle de deduplicação anti-spam (evita reenvio do mesmo prazo no mesmo dia).
  - `app_logs`: auditoria e diagnósticos de execução.

### 3c.3 Agendador em Segundo Plano (`app/scheduler.py`)
- Thread daemon autônoma com `threading.Event()` (`self._wake_event`).
- Permite alterar o período de atualização em tempo real (ex: de 60m para 15m) e acordar o ciclo imediatamente via `scheduler.restart_cycle()`, sem esperar o timer anterior expirar.
- Classificação inteligente de prazos ([`app/notifier/formatter.py`](file:///z:/pol/app/notifier/formatter.py)):
  - `Hoje` (vencendo na data atual)
  - `Amanhã / Próximos 2 dias`
  - `Esta Semana` (até o fim do domingo corrente)
  - `Atrasadas / Pendentes`

### 3c.4 Despachantes de Notificação
- **E-mail (SMTP):** Template HTML responsivo em dark mode com cartões por criticidade e envio via TLS/SSL ([`app/notifier/email_service.py`](file:///z:/pol/app/notifier/email_service.py)).
- **Telegram:** Mensagens estruturadas em Markdown via Telegram Bot API ([`app/notifier/telegram_service.py`](file:///z:/pol/app/notifier/telegram_service.py)).
- **WhatsApp:** Integração REST com servidores OpenWA / WPPConnect / Z-API ([`app/notifier/openwa_service.py`](file:///z:/pol/app/notifier/openwa_service.py)).
- Todos os canais possuem botões **"Testar Envio"** com alertas modais nativos no painel de configurações.

---

## 4. Estrutura de Arquivos

```
z:\pol\
│   app-web.py                     # Versão Web / Cloudflare Worker (Python / Pyodide)
│   wrangler.toml                  # Deploy no Cloudflare Workers (cron */5, KV POL_STORE sem id = provisionamento automático, service binding SMTP)
│   smtp-worker/                   # Worker JS auxiliar pol-adv-smtp: SMTP com TLS/STARTTLS por RPC (smtp.js, index.js, test.mjs)
│   release.ps1                    # Cria e envia a próxima tag vX.Y.Z (dispara o CI)
│   server.py / index.html         # Proxy CORS e Swagger UI (porta 8000)
│   download_specs.py              # Baixa os OpenAPI do POL (precisa de POL_TOKEN no ambiente)
│   run_app.bat, iniciar_*.bat     # Atalhos (app Python legado, Swagger, web local)
│   CLAUDE.md, AGENTS.md, GEMINI.md  # Guias para assistentes (manter os três sincronizados)
│   assets/                        # Logo do app (original e derivadas)
│
├── desktop-wpf/                   # Desktop .NET 10 WPF (edição ativa)
│   ├── PolNotifier.sln, nuget.config, build.ps1, publish.ps1, README.md
│   ├── installer/PolNotifier.nsi  # Instalador NSIS (por usuário)
│   └── PolNotifier/               # Projeto: App.xaml(.cs), Core/*.cs, Ui/*, app.ico, app.manifest
│
├── .github/workflows/             # desktop.yml (build + release com instalador) e web.yml (deploy do Worker)
├── .spec-kit/                     # Spec-Driven Development (constitution, spec, plan, tasks)
├── app/                           # Desktop Python/CustomTkinter (legado, só referência)
├── data/                          # NÃO versionado: pol_notifier.db (global) e users/<sub>.db
└── swagger/                       # NÃO versionado: OpenAPI do POL (gerado por download_specs.py)
```

---

## 5. Como Executar os Componentes

```powershell
.\desktop-wpf\build.ps1                          # compila o desktop .NET
dotnet run --project desktop-wpf\PolNotifier      # roda o desktop .NET
.\desktop-wpf\publish.ps1 -Version 2.1.0         # .exe single-file + instalador NSIS
.\release.ps1 [-Bump minor|major] [-DryRun]       # lança a próxima versão (tag vX.Y.Z → CI)
python app-web.py                                 # web local
python server.py                                  # Swagger UI + proxy CORS (http://127.0.0.1:8000)
python -m app.main                                # desktop Python (legado; não rodar junto com o .NET)
```

O deploy do Worker é feito pelo CI a cada push que toque `app-web.py`/`wrangler.toml` (manual: `npx wrangler deploy`).

---

## 6. Histórico de Decisões e Armadilhas Superadas (Gotchas)

1. **Minimização no `Win + D`:**
   - *Tentativa anterior:* Usar `SetWindowPos(HWND_BOTTOM)` e capturar `<Unmap>`. O Windows Explorer ainda forçava a minimização de janelas top-level.
   - *Tentativa 2:* Reparentar via `SetParent` para o `Progman` (técnica do PDF). Funcionava até o 23H2; no 24H2+ o `Progman` tem `WS_EX_NOREDIRECTIONBITMAP` e o widget fica invisível.
   - *Solução atual:* Owner = `Progman` (`GWLP_HWNDPARENT`) + `HWND_BOTTOM`. O Stardock Fences consegue usar filhos do desktop porque roda dentro do explorer.exe (`DesktopDock64.dll` injetada via Detours, com tratamento explícito de `AllowNoRedirectBitmap`), o que não é viável em Python. Decompilação (REA + Ghidra 12.1.4, `FUN_1801b5d50`, WndProc instalado no desktop) mostra que, no modo "Magic CustomOwner", o Fences cria uma janela própria (`hwndMagicOwner`) e faz `SetParent` da **view do desktop do próprio Explorer** para dentro dela, recriando-a quando o estado de `NoRedirectBitmap` muda. Ou seja, ele muda a hierarquia do Explorer por dentro, em vez de colocar janelas suas dentro do `Progman`. O Win + D é detectado via `WM_HSHELL_WINDOWACTIVATED` (foreground passa a ser `WorkerW`), e a z-order é protegida interceptando `WM_WINDOWPOSCHANGING` nas janelas marcadas com a prop `FencesElement`.
2. **Crash `PyEval_RestoreThread` (Python 3.14):**
   - *Causa:* Chamar `SetParent` em uma janela `CTkToplevel` filha de outra janela Tkinter causava conflito de sincronização e perda de GIL durante os callbacks de `WndProc`.
   - *Solução:* Tornar o `DesktopFenceWidget` a raiz (`ctk.CTk`) da aplicação e fazer as chamadas Win32 de fixação em uma thread separada (`PyDLL` sozinho não resolve: ainda crashava).
3. **Multi-monitores e Posição do Widget:**
   - Clampar coordenadas por `winfo_screenwidth()` quebrava layouts multi-telas e posições na borda direita. Coordenadas salvas no SQLite agora são preservadas de forma exata.
4. **Fechamento e Minimização para a Bandeja:**
   - Implementadas as opções configuráveis `minimize_to_tray` e `close_to_tray` com switches dedicados na interface.
5. **403 do gateway (desktop e web):** o nginx do POL recusa requisições sem `User-Agent`; o `HttpClient` do .NET e o `fetch` do Worker não enviam nenhum por padrão (o `http_request` do Worker define `PolNotifier-Web/2.1`). Na web, falha de busca/sessão agora aparece na tela (`/api/sync` devolve `error`) em vez de contadores zerados.
6. **Horários errados:** a API manda UTC (`02:59:59Z` = 23:59 em Brasília). Classificar/exibir pela data UTC joga o prazo para o dia seguinte; converter para o fuso local e esconder a hora quando for "fim do dia".
7. **Testar e-mail sem salvar:** o teste lia usuário/senha do que estava gravado e falhava com "5.7.0 Authentication Required". Os botões "Testar" agora salvam os campos antes.
8. **Web "não salva a senha / não sincroniza":** faltava o KV `POL_STORE` (estava comentado no `wrangler.toml`); sem ele cada requisição começa com o estado vazio e o cron não tem usuários. Hoje o binding é declarado sem `id` e o wrangler provisiona o namespace no deploy.
9. **Segredos do GitHub via PowerShell 5.1:** `valor | gh secret set` grava BOM/CRLF e o wrangler recusa o `CLOUDFLARE_ACCOUNT_ID`. O `web.yml` filtra para `[A-Za-z0-9_-]`.
10. **PowerShell:** scripts sem BOM são lidos como ANSI; uma função com o nome de um executável (`Git`) chama a si mesma. NuGet global sem fontes: o `nuget.config` fica em `desktop-wpf/`.
11. **Repositório público:** o JWT que estava fixo em `index.html`/`download_specs.py` foi removido (`POL_TOKEN` no ambiente / campo na interface); `data/`, `.env` e `swagger/*.json` ficam no `.gitignore`.

---

## 7. CI/CD e Releases

- **Desktop (`.github/workflows/desktop.yml`):** em push/PR que toque `desktop-wpf/**`, compila e roda `publish.ps1`; em tag `v*`, cria a release (*latest*) com `PolNotifier-Setup-X.Y.Z.exe`, `PolNotifier-Setup.exe` (nome fixo) e `PolNotifier.exe`. O instalador NSIS (`desktop-wpf/installer/PolNotifier.nsi`) é por usuário, sem administrador, com opções de atalho na Área de Trabalho, pasta no Menu Iniciar e início com o Windows.
- **Web (`.github/workflows/web.yml`):** `py_compile`, `node smtp-worker/test.mjs`, `wrangler deploy` do Worker auxiliar `pol-adv-smtp` e depois do principal (ordem importa: service binding), em push que toque `app-web.py`/`wrangler.toml`/`smtp-worker/**` e em tags. Segredos: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`.
- **Versão:** `release.ps1` incrementa a última tag `vX.Y.Z` (patch por padrão) e envia; o CI faz o resto. Limite da API do GitHub: 5.000 requisições/h.
12. **SMTP no Worker Python:** `asyncio.open_connection(..., ssl=True)` falha com "SSL/TLS via create_connection is not yet supported" (a porta 465 exige TLS). Solução: Worker JavaScript `pol-adv-smtp` (`cloudflare:sockets` com `secureTransport`) chamado por RPC pelo Python Worker (service binding `SMTP`). O payload vai como **texto JSON** (`rpc.send(json.dumps(...))`): passar objeto via `to_js(..., dict_converter=Object.fromEntries)` falhou em produção com "Result should have type object not function". `workers_dev = false` nele: só acessível por binding.
