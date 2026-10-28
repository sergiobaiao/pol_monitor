# GEMINI.md

Guia para o Gemini CLI neste repositório. Contexto detalhado (APIs, estrutura, gotchas, CI/CD): [`AGENTS.md`](AGENTS.md). Guia equivalente para o Claude Code: [`CLAUDE.md`](CLAUDE.md).

> **Regra de manutenção:** ao modificar qualquer coisa relevante (código, build, CI/CD, configuração, comportamento), atualize **`CLAUDE.md`, `AGENTS.md` e `GEMINI.md`** na mesma alteração. Os três descrevem o mesmo projeto e não podem divergir. Mudanças de escopo também vão para `.spec-kit/spec.md` e `tasks.md`.

**POL ADV Notifier** monitora prazos e audiências da plataforma jurídica POL ADV (`office-api.pol.adv.br`) e notifica por E-mail, Telegram e WhatsApp. Repositório público: https://github.com/sergiobaiao/pol_monitor — nunca versionar segredos, `.env`, `data/`, `swagger/*.json` nem o JWT do POL. Código, comentários, logs e textos de UI em português.

## Componentes
- **`desktop-wpf/`** — desktop **.NET 10 + WPF** (edição ativa; substitui `app/`): login no Keycloak com senha no Credential Manager (formato do `keyring`), SQLite por usuário em `data/users/<sub>.db`, agendador, widget estilo Fences, painel (Início, Tarefas Lista/Kanban, Calendário Mês/Lista com popup de detalhes ao clicar, Notificações, Configurações, Logs), bandeja, instalador NSIS.
- **`app-web.py`** — web autocontida (SPA embutida) e Python Worker no Cloudflare; persistência no KV `POL_STORE`; e-mail por OAuth (Gmail API) ou senha de app do Google (SMTP). O Python Worker não faz TLS em sockets, então o SMTP passa por RPC (service binding `SMTP`, payload em texto JSON) para o Worker JavaScript `pol-adv-smtp` em `smtp-worker/` (testar com `node smtp-worker/test.mjs`). **Não importa nada do desktop**: regra de negócio corrigida num lado deve ser replicada no outro.
- **Logo:** `assets/app-icon-original.png`; derivadas em `desktop-wpf/PolNotifier/app.ico`, `logo.png` e `LOGO_B64` (`app-web.py`). A web também tem Tarefas Lista/Kanban e Calendário Mês/Lista (`GET /api/board`).
- **Canais:** interruptor "Ativo" por canal (e-mail, Telegram, WhatsApp) no desktop e na web; só os ativos recebem o relatório (web: `migrate_channel_flags` liga uma vez quem já tinha dados).
- **Paridade desktop × web:** e-mail, Telegram, WhatsApp e assunto têm que sair idênticos. O formatador existe em C# (`Formatter.cs`) e em Python (`app-web.py`: `email_html`, `format_telegram`, `format_openwa`, `email_subject`, `urgency_color`, `fmt_deadline`): mudou um, mude o outro e compare as saídas.
- **`server.py` + `index.html` + `swagger/`** — Swagger UI e proxy CORS do POL.
- **`app/`** — desktop antigo em Python/CustomTkinter, só referência. Não rodar junto com o .NET (duplica agendador e notificações).

## Comandos
```powershell
.\desktop-wpf\build.ps1                          # compila (deve dar 0 warnings)
dotnet run --project desktop-wpf\PolNotifier      # roda
.\desktop-wpf\publish.ps1 -Version 2.1.0         # .exe single-file + instalador NSIS (se houver makensis)
.\release.ps1 [-Bump minor|major] [-DryRun]       # cria e envia a próxima tag vX.Y.Z; o CI publica a release e faz o deploy web
python -c "import py_compile; py_compile.compile('app-web.py', doraise=True)"
python app-web.py                                 # web local
```
Não há suíte de testes. O `nuget.config` de `desktop-wpf/` aponta para o nuget.org.

## Regras que custaram caro
- **Widget (`Ui/FenceWindow`)**: janela top-level `WS_EX_TOOLWINDOW` com **owner = `Progman`** (`GWLP_HWNDPARENT`) em `HWND_BOTTOM`; timer de 1 s refaz o owner. **Nunca `SetParent`** no Progman/WorkerW (Win 11 24H2+: `WS_EX_NOREDIRECTIONBITMAP`, filhos de outro processo não são desenhados) e **nunca restaurar o widget por conta própria** (`Show`/`WindowState=Normal`): o 2º Win + D deixaria de devolver as janelas. Posição em pixels físicos, sem clamp por tela.
- **API do POL**: o gateway responde `403` sem `User-Agent`. Datas vêm em **UTC** (`02:59:59Z` = 23:59 em Brasília): converter para o fuso local antes de classificar; prazos de "fim do dia" mostram só a data. Paginar com `size=100&page=N`.
- **Segredos**: senha e tokens nunca vão para o SQLite. A senha lembrada fica no Credential Manager (alvo `POL ADV Notifier`, usuário = e-mail, blob UTF-16).
- **E-mail**: título/assunto `Publicações Online - Relatório de Tarefas Diário para dd/MM/yyyy` (prefixo `URGENTE:` se há prazo hoje); seção "Hoje" sempre primeiro; tabelas e estilos inline; cores pelo gradiente de urgência (verde ≥7 dias → vermelho hoje; atrasado vinho; sem prazo cinza; contraste ≥ 4,5:1 no e-mail). Telegram/WhatsApp usam emojis ⛔🔴🟠🟡🟢⚪ no lugar de cor.
- **Botões "Testar"** salvam os campos antes de enviar; **"Enviar último relatório"** (desktop e web) reenvia para os canais ativos ignorando a deduplicação diária.
- **Web**: sem o KV `POL_STORE` cada requisição começa vazia ("Lembrar" some, o cron não acha usuários). O `wrangler.toml` declara o binding **sem `id`** (provisionamento automático no deploy).
- **PowerShell 5.1**: `.ps1` sem BOM é lido como ANSI; função com nome de executável recursa (`Invoke-Git`); `valor | gh secret set` grava BOM/CRLF (o `web.yml` filtra).

## CI/CD (`.github/workflows/`)
- `desktop.yml` (windows): build + `publish.ps1` em mudanças de `desktop-wpf/**`; em tag `v*` cria a release *latest* com `PolNotifier-Setup-X.Y.Z.exe`, `PolNotifier-Setup.exe` e `PolNotifier.exe`.
- `web.yml` (ubuntu): `py_compile`, teste do `smtp-worker`, deploy do Worker auxiliar e depois do principal, em mudanças de `app-web.py`/`wrangler.toml`/`smtp-worker/**` e em tags. Segredos: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`.
- Instalador NSIS (`desktop-wpf/installer/PolNotifier.nsi`): por usuário, sem administrador; opções de atalho na Área de Trabalho, pasta no Menu Iniciar e início com o Windows. Limite da API do GitHub: 5.000 requisições/h.
