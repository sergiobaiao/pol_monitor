# Specification — POL ADV Notifier Desktop

## 1. Visão Geral do Produto
O **POL ADV Notifier** é uma aplicação desktop nativa para Windows desenvolvida em Python. O sistema roda de maneira silenciosa na bandeja do sistema (System Tray) e se integra à API do **POL ADV** (Publicações Online) para monitorar prazos processuais, tarefas e eventos da agenda. Ele dispara notificações consolidadas e organizadas por seções temporais através de **E-mail (SMTP)**, **Telegram** e **WhatsApp (OpenWA)**.

---

## 2. Requisitos Funcionais (FR)

### FR-01: Autenticação Automática no POL ADV
- As duas edições (desktop e web) só abrem após login com e-mail e senha da conta POL ADV, validados direto no Keycloak; senha errada mostra "E-mail ou senha incorretos.".
- Cada usuário (chave = `sub` do JWT) tem configurações, cache, histórico e logs próprios. Desktop: `data/users/<sub>.db`; o banco legado `data/pol_notifier.db` é migrado para o primeiro usuário que for dono dele e passa a guardar só o e-mail lembrado.
- Checkbox "Lembrar usuário/senha": no desktop a senha vai para o Windows Credential Manager (`keyring`); na web só o refresh token (`offline_access`) e um cookie `HttpOnly`/`SameSite=Strict` de 30 dias. Desmarcado = esquece. A senha nunca é gravada no SQLite/JSON.
- Na web, o e-mail sai pela Gmail API com OAuth do próprio usuário (cliente OAuth criado por ele, guiado na aba Notificações); nenhuma senha de e-mail é guardada.
- O cron do Cloudflare notifica cada usuário que marcou "Lembrar" (refresh token salvo no KV `POL_STORE`).
- A aplicação deve realizar a autenticação direta contra o Keycloak (`https://keycloak.pol.adv.br/auth/realms/pol-prod/protocol/openid-connect/token`) usando `grant_type=password` e `client_id=web_app`.
- A aplicação deve renovar o token de acesso (via refresh token ou reautenticação automática) antes de sua expiração (TTL de 8h).
- O sistema deve resolver automaticamente o Tenant ID (`x-officeid`) e o UUID do Usuário (`x-selecteduserid`).

### FR-02: Coleta de Tarefas e Calendário
- A aplicação deve consultar periodicamente:
  - Tarefas ativas (`/services/lawoffice/api/task/tasks`).
  - Eventos de agenda (`/services/lawoffice/api/calendar/events`).
- Os itens coletados devem ser cacheados e normalizados localmente no banco SQLite.

### FR-03: Classificação por Seções de Prazo
- O motor de regras deve agrupar tarefas e eventos nas seguintes seções:
  1. **🚨 Vencendo Hoje:** Itens com prazo/data igual ao dia corrente.
  2. **⚠️ Vencendo Amanhã / Próximos Dias:** Itens com vencimento entre D+1 e D+N (configurável, padrão D+2).
  3. **📅 Vencendo Esta Semana:** Itens com vencimento nos próximos 7 dias.
  4. **⏳ Tarefas Pendentes / Atrasadas:** Itens cujo prazo já expirou e ainda não foram concluídos/arquivados.

### FR-04: Despacho Multicanal de Notificações
- **Canal 1 — E-mail (SMTP):**
  - Envio formatado em HTML responsivo com cabeçalho limpo, cores indicativas de urgência e tabela/cards por seção.
  - Suporte a múltiplos destinatários (separados por vírgula).
  - Suporte a SMTP com TLS/SSL (porta 587/465) com autenticação de usuário/senha.
- **Canal 2 — Telegram:**
  - Envio via Telegram Bot API utilizando Bot Token e Chat ID (ou IDs múltiplos).
  - Formatação Markdown estruturada com emojis e links.
- **Canal 3 — WhatsApp (OpenWA):**
  - Integração via REST API com servidores OpenWA / WPPConnect (`POST /sendText`).
  - Envio para números individuais (ex: `5511999999999@c.us`) ou grupos (`...-group@c.us`).

### FR-05: Agendamento e Prevenção de Spam (Deduplicação)
- Intervalo de checagem configurável (ex: a cada 60 minutos, ou em horários fixos do dia como 08:00 e 14:00).
- Registro no SQLite de cada notificação enviada para evitar reenvios duplicados do mesmo item na mesma seção no mesmo dia civil.

### FR-06: Interface Gráfica Desktop & Bandeja do Sistema
- Janela desktop moderna com abas:
  - **Aba 1: Dashboard:** Status da conexão, última verificação, resumo de prazos (Hoje, Amanhã, Semana) e botão "Sincronizar & Notificar Agora".
  - **Aba 2: Configuração POL ADV:** Login, Senha, Office ID, status do token e botão "Testar Conexão".
  - **Aba 3: Configuração de Notificações:** SMTP, Telegram e OpenWA, com botão de teste individual para cada canal.
  - **Aba 4: Prazos e Tarefas:** Tabela com busca e filtro dos itens coletados.
  - **Aba 5: Histórico & Logs:** Registro de eventos e notificações enviadas.
- Ao fechar a janela (`X`), a aplicação minimiza para o System Tray em vez de fechar.
- Menu de contexto na bandeja: "Abrir Painel", "Verificar Agora", "Silenciar", "Sair".

---

## 3. Requisitos Não-Funcionais (NFR)
- **NFR-01:** Execução nativa em Windows 10/11 x64, sem dependências de ambientes pesados.
- **NFR-02:** Persistência em banco de dados SQLite (`pol_notifier.db`).
- **NFR-03:** Thread de background assíncrona para que tarefas de rede não congelem a UI.
- **NFR-04:** Tolerância a falhas de rede (tentativa posterior sem crash).
