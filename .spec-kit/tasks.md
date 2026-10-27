# Tasks & Implementation Checklist — POL ADV Notifier Desktop

## Fase 1: Infraestrutura e Camada de Dados
- [x] **T-1.1:** Definir estrutura do projeto e dependências em `requirements.txt`.
- [x] **T-1.2:** Implementar `app/database.py` com esquema SQLite completo (`settings`, `cached_items`, `notification_history`, `app_logs`).
- [x] **T-1.3:** Implementar `app/config.py` para gerenciamento centralizado de configurações e persistência reativa.

## Fase 2: Integração com a API do POL ADV
- [x] **T-2.1:** Implementar `app/pol_client.py` com suporte ao fluxo OAuth2 Direct Grant no Keycloak (`username`, `password`, `refresh_token`).
- [x] **T-2.2:** Implementar captura automática de metadados de sessão (`x-officeid`, `x-selecteduserid`, perfil do usuário).
- [x] **T-2.3:** Implementar métodos para buscar tarefas ativas (`/services/lawoffice/api/task/tasks`) e eventos de calendário (`/services/lawoffice/api/calendar/events`).

## Fase 3: Serviços de Notificação Multicanal
- [x] **T-3.1:** Implementar formatador de seções de prazos (`Hoje`, `Amanhã/Próximos Dias`, `Semana`, `Atrasados`).
- [x] **T-3.2:** Implementar `app/notifier/email_service.py` com template HTML responsivo por seções e envio SMTP/TLS.
- [x] **T-3.3:** Implementar `app/notifier/telegram_service.py` com formatação Markdown e integração com Telegram Bot API.
- [x] **T-3.4:** Implementar `app/notifier/openwa_service.py` com integração REST para servidores OpenWA / WPPConnect.
- [x] **T-3.5:** Implementar lógica de teste imediato para cada canal (botões "Testar Envio").

## Fase 4: Motor de Agendamento e Deduplicação
- [x] **T-4.1:** Implementar `app/scheduler.py` como daemon thread com suporte a intervalo configurável e disparo manual.
- [x] **T-4.2:** Integrar verificação de duplicidade através da tabela `notification_history` do SQLite.

## Fase 5: Interface Gráfica Desktop & System Tray (Estilo Windows Widgets)
- [x] **T-5.1:** Implementar `app/ui/main_window.py` com CustomTkinter (tema escuro Fluent, barra de navegação lateral com abas Widgets, Tarefas, Calendário, Notificações, Configurações e Logs).
- [x] **T-5.2:** Implementar `app/ui/tray_icon.py` com `pystray` (minimizar para a bandeja ao fechar `X`, menu de contexto com opções rápidas e restauração).
- [x] **T-5.3:** Implementar `app/ui/desktop_fence.py` — Widget flutuante de Desktop estilo Stardock Fences (arrastável, translúcido/acrílico, fixado na camada `HWND_BOTTOM`, resistente a Win+D, 4 mini-cards e lista de prazos em tempo real).
- [x] **T-5.4:** Implementar sincronização com a cor de destaque do Windows (`theme.py`), redimensionamento visual via alça `⤡` e sliders de transparência/tamanho.
- [x] **T-5.5:** Configurar inicialização silenciosa na bandeja (`start_minimized`), exibindo apenas o widget no desktop por padrão (ambos configuráveis).
- [x] **T-5.6:** Criar script inicializador `run_app.bat` e ponto de entrada `app/main.py`.
- [x] **T-5.7:** Implementar comportamentos configuráveis de janela: Minimizar para a bandeja ao clicar em '_' (`minimize_to_tray`) e Fechar para a bandeja ao clicar em 'X' (`close_to_tray`).
- [x] **T-5.8:** Implementar Reparenting nativo (`SetParent`) para o contêiner de Desktop (`WorkerW` / `Progman`), garantindo imunidade total contra `Win + D` e desvinculação completa do ciclo de vida da janela principal.
- [x] **T-5.9:** Implementar seção dedicada para configuração da frequência e período de atualizações do app com atalhos de predefinição rápida (5m, 15m, 30m, 1h, 2h, 4h, 8h), slider com ajuste fino e aplicação instantânea no scheduler via threading events.

## Fase 6: Testes Integrados e Validação
- [x] **T-6.1:** Testar autenticação e sincronização real contra a API do POL (98 tarefas e 1 evento sincronizados com sucesso).
- [x] **T-6.2:** Validar classificação em seções (0 hoje, 2 próximos dias, 1 semana, 13 atrasadas).
- [x] **T-6.3:** Validar formatação HTML de e-mail, Markdown do Telegram e texto do OpenWA.

## Fase 7: Versão 100% Web & Cloudflare Workers (`app-web.py`)
- [x] **T-7.1:** Implementar `app-web.py` com suporte híbrido: execução local (`python app-web.py`) e na borda do Cloudflare (`on_fetch`, `on_scheduled`).
- [x] **T-7.2:** Criar interface SPA responsiva completa embutida (Dashboard, Tarefas com busca/filtros, Calendário, Notificações, Configurações e Logs).
- [x] **T-7.3:** Configurar `wrangler.toml` com suporte nativo a Python Workers e gatilhos de agendamento automático (Cron Triggers).

## Fase 8: Login e Multiusuário
- [x] **T-8.1:** Tela de login no desktop (`app/ui/login_window.py`) antes de montar Scheduler/Fence/MainWindow; "Trocar Usuário" na bandeja e nas Configurações reinicia tudo com o novo usuário.
- [x] **T-8.2:** Banco SQLite por usuário (`data/users/<sub>.db`) com migração do banco legado; senha e tokens fora do SQLite.
- [x] **T-8.3:** "Lembrar usuário/senha" via Windows Credential Manager (`keyring`).
- [x] **T-8.4:** Login na web (`/api/login`, cookie de sessão), estado por usuário em `data/web_store.json` / KV `POL_STORE`, cron iterando usuários lembrados.
- [x] **T-8.5:** Remover o JWT de bootstrap hardcoded de `app/main.py`, `app-web.py` e `server.py` (agora `POL_TOKEN` opcional).
- [ ] **T-8.6:** Criar o KV `POL_STORE` no Cloudflare e descomentar o binding em `wrangler.toml`.
- [x] **T-8.7:** Worker funcional: HTTP via `fetch` do Workers (async), `WorkerEntrypoint`, monitor 24h por usuário com intervalo e deduplicação diária (cron `*/5` + thread local).
- [x] **T-8.8:** E-mail na web via Gmail API (OAuth por usuário, instruções na aba Notificações), incluído no monitor 24h e no teste de canal.
- [ ] **T-8.9:** Teste real do Gmail OAuth com um cliente criado pelo usuário.
