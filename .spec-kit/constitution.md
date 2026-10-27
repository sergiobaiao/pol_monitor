# Constitution — POL ADV Notifier Desktop

## 1. Princípios Fundamentais (Tenets)

1. **Segurança de Credenciais e Privacidade:**
   - As credenciais de acesso ao POL ADV (e-mail, senha, tokens) e configurações de canais de notificação (SMTP, chaves Telegram, endpoints OpenWA) devem ser armazenadas de forma segura e local no banco de dados SQLite.
   - Nenhuma credencial ou dado confidencial processual deve ser transmitido a servidores terceiros além dos canais explicitamente configurados pelo usuário.

2. **Confiabilidade e Não-Intrusividade:**
   - A aplicação deve operar primariamente em segundo plano na bandeja do sistema (System Tray Icon).
   - O consumo de CPU e memória deve ser mínimo enquanto em repouso.
   - Falhas transitórias de conexão com a API do POL ou servidores de e-mail não devem travar a interface gráfica nem abortar o loop de agendamento. Devem ser tratadas com retentativas e log detalhado.

3. **Arquitetura Desacoplada e Testável:**
   - Separação estrita entre Camada de Apresentação (GUI Desktop Tkinter/pystray), Camada de Negócio/Orquestração (Scheduler & Notifiers), Camada de Dados (SQLite Persistence) e Camada de Integração (POL Client, SMTP, Telegram, OpenWA).
   - Cada despachante de notificação deve implementar uma interface comum e responder a testes de envio sob demanda na interface.

4. **Transparência de Estado:**
   - O usuário deve conseguir inspecionar a qualquer momento: status da conexão, último horário de sincronização, próximas tarefas a vencer e histórico de notificações enviadas.

5. **Adesão ao Spec-Driven Development (SDD):**
   - Todas as decisões de design, requisitos funcionais e tarefas de implementação devem ser rastreáveis através dos artefatos do Spec-Kit (`constitution.md`, `spec.md`, `plan.md`, `tasks.md`).
