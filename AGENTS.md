# AGENTS.md — Guia de Arquitetura e Continuidade (POL ADV Notifier)

Este documento foi elaborado para guiar qualquer agente de IA ou desenvolvedor na continuidade, manutenção e evolução deste projeto.

---

## 1. Visão Geral do Projeto

O **POL ADV Notifier** é um ecossistema completo para monitoramento automatizado, visualização e alertas de prazos processuais e audiências da plataforma jurídica **POL ADV** (`https://office.pol.adv.br/`).

O projeto é composto por:
1. **Desktop App (Nativo Windows)**: Aplicação com tema escuro Fluent (CustomTkinter), integração com a bandeja do sistema (`pystray`), agendador em background, notificações multicanal (E-mail, Telegram, WhatsApp) e um **Widget de Área de Trabalho estilo Stardock Fences**.
2. **Swagger UI & Proxy Local**: Servidor local em `http://127.0.0.1:8000` (`server.py`) com especificações OpenAPI 3.0 consolidadas das 476 rotas da API do POL ADV e proxy CORS reverso.
3. **Web Edition & Cloudflare Workers**: Aplicação 100% web (`app-web.py`) com SPA embutida, pronta para execução local ou deploy serverless no Cloudflare Workers via Python Workers / Pyodide (`wrangler.toml`).

---

## 2. Topologia de APIs e Autenticação (POL ADV)

- **Portal Web:** `https://office.pol.adv.br/`
- **API Gateway:** `https://office-api.pol.adv.br`
- **Servidor Keycloak (OAuth2 Direct Grant):** `https://keycloak.pol.adv.br/auth/realms/pol-prod`
  - Token Endpoint: `/protocol/openid-connect/token`
  - Client ID: `web_app` (Direct Grant: `grant_type=password`, `username`, `password`)
  - Auto-refresh de tokens via `refresh_token` implementado em [`app/pol_client.py`](file:///z:/pol/app/pol_client.py).
- **Tenant ID (`x-officeid`):** `25324` (capturado automaticamente dos claims do JWT ou configurado manualmente).
- **Usuário Padrão:** Sérgio Baião (`sergiobaiao@gmail.com`), UUID: `46e0695a-490e-49f4-b660-d19af55d956d`.
- **Rotas Principais da API:**
  - Tarefas ativas: `GET /services/lawoffice/api/task/tasks?active=true`
  - Compromissos / Audiências: `GET /services/lawoffice/api/calendar/events`

---

## 3. Arquitetura do Desktop App (Windows)

### 3.1 Inversão Arquitetural de Janelas (Tkinter & Win32)
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

### 3.2 Persistência e Banco de Dados Local
- **Arquivo:** `z:\pol\data\pol_notifier.db` (SQLite com `WAL mode`).
- **Tabelas (`app/database.py`):**
  - `settings`: pares chave-valor para credenciais, preferências e geometria do widget.
  - `cached_items`: cópia local de tarefas e audiências para inicialização instantânea offline.
  - `notification_history`: controle de deduplicação anti-spam (evita reenvio do mesmo prazo no mesmo dia).
  - `app_logs`: auditoria e diagnósticos de execução.

### 3.3 Agendador em Segundo Plano (`app/scheduler.py`)
- Thread daemon autônoma com `threading.Event()` (`self._wake_event`).
- Permite alterar o período de atualização em tempo real (ex: de 60m para 15m) e acordar o ciclo imediatamente via `scheduler.restart_cycle()`, sem esperar o timer anterior expirar.
- Classificação inteligente de prazos ([`app/notifier/formatter.py`](file:///z:/pol/app/notifier/formatter.py)):
  - `Hoje` (vencendo na data atual)
  - `Amanhã / Próximos 2 dias`
  - `Esta Semana` (até o fim do domingo corrente)
  - `Atrasadas / Pendentes`

### 3.4 Despachantes de Notificação
- **E-mail (SMTP):** Template HTML responsivo em dark mode com cartões por criticidade e envio via TLS/SSL ([`app/notifier/email_service.py`](file:///z:/pol/app/notifier/email_service.py)).
- **Telegram:** Mensagens estruturadas em Markdown via Telegram Bot API ([`app/notifier/telegram_service.py`](file:///z:/pol/app/notifier/telegram_service.py)).
- **WhatsApp:** Integração REST com servidores OpenWA / WPPConnect / Z-API ([`app/notifier/openwa_service.py`](file:///z:/pol/app/notifier/openwa_service.py)).
- Todos os canais possuem botões **"Testar Envio"** com alertas modais nativos no painel de configurações.

---

## 4. Estrutura de Arquivos

```
z:\pol\
│   app-web.py                     # Versão Web / Cloudflare Worker (Python / Pyodide)
│   wrangler.toml                  # Configuração de deploy no Cloudflare Workers
│   run_app.bat                    # Script para rodar o app desktop nativo
│   iniciar_swagger.bat            # Script para subir o Swagger UI + CORS Proxy
│   iniciar_web.bat                # Script para subir a versão Web local
│   server.py                      # Servidor proxy CORS e Swagger UI (porta 8000)
│   index.html                     # Interface Swagger UI com abas POL ADV
│   requirements.txt               # Dependências Python (requests, customtkinter, pystray, pillow, pywin32)
│   AGENTS.md                      # Este arquivo de referência
│   CLAUDE.md                      # Guia de comandos e estilo para assistentes
│
├── .spec-kit/                     # Metodologia Spec-Driven Development (SDD)
│   ├── constitution.md            # Princípios e regras inegociáveis
│   ├── spec.md                    # Especificação funcional detalhada
│   ├── plan.md                    # Arquitetura e fases do projeto
│   └── tasks.md                   # Checklist de tarefas executadas
│
├── app/                           # Código fonte do Desktop App
│   ├── main.py                    # Entry point e inicialização
│   ├── config.py                  # Gerenciador de configurações e propriedades reativas
│   ├── database.py                # Camada SQLite (settings, cached_items, logs)
│   ├── pol_client.py              # Cliente HTTP Keycloak + API Gateway do POL
│   ├── scheduler.py               # Motor de agendamento em background
│   │
│   ├── notifier/                  # Serviços de Notificação
│   │   ├── formatter.py           # Formatador de seções e gerador HTML/Markdown/Texto
│   │   ├── email_service.py       # Despachante SMTP com template HTML
│   │   ├── telegram_service.py    # Despachante Telegram Bot API
│   │   └── openwa_service.py      # Despachante WhatsApp (OpenWA / WPPConnect)
│   │
│   └── ui/                        # Interface Gráfica (CustomTkinter)
│       ├── desktop_fence.py       # Widget de Desktop raiz fixado via owner = Progman
│       ├── main_window.py         # Painel de controle Toplevel com abas e settings
│       ├── tray_icon.py           # Gerenciador do ícone na bandeja (pystray)
│       └── theme.py               # Leitor da cor de destaque do Windows (DWM)
│
├── data/                          # Dados locais persistentes
│   └── pol_notifier.db            # Banco SQLite local
│
└── swagger/                       # Especificações OpenAPI do POL ADV
    ├── lawoffice-service-api.json # 476 rotas da API principal
    ├── polgateway-api.json        # Rotas do gateway
    └── polgateway-management-api.json
```

---

## 5. Como Executar os Componentes

### 🖥️ Desktop App Nativo
```powershell
# Execução direta
python -m app.main

# Ou via batch
.\run_app.bat
```

### 🌐 Swagger UI + Proxy CORS
```powershell
python server.py
# Acessar em: http://127.0.0.1:8000
```

### ☁️ Versão Web Local
```powershell
python app-web.py
# Acessar em: http://127.0.0.1:8787
```

### 🚀 Deploy no Cloudflare Workers
```powershell
npx wrangler deploy
```

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
