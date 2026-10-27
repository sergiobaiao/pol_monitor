# POL ADV — Desktop Notifier & Fences Widget

Aplicação Desktop nativa para Windows desenvolvida sob a metodologia **[Spec-Kit / SDD](https://github.com/github/spec-kit)** para monitoramento contínuo, notificações multicanal (E-mail, Telegram, WhatsApp) e exibição de prazos jurídicos da plataforma **POL ADV** ([office.pol.adv.br](https://office.pol.adv.br)).

---

## 🌟 Funcionalidades Principais

1. **🖥️ Widget de Desktop (Estilo Stardock Fences):**
   * Widget translúcido com efeito acrílico e cantos arredondados que repousa diretamente na Área de Trabalho.
   * **Arrastável:** Clique e arraste para posicionar em qualquer monitor (a posição `x, y` é lembrada automaticamente).
   * **Recolhível (Roll-up):** Dê um duplo clique no cabeçalho ou clique em `−` para recolher para uma barra compacta (recurso clássico do Fences).
   * **4 Mini-Cards no Topo:** Exibição em tempo real de `🚨 HOJE`, `⚠️ +2 DIAS`, `📅 SEMANA` e `⏳ ATRASO`.
   * **Lista de Prazos:** Cards compactos com prazos e números de processos.

2. **📊 Painel Central (Estilo Windows 11 Widgets):**
   * Barra lateral com navegação em abas: *Widgets/Início*, *Tarefas*, *Calendário*, *Notificações*, *Configurações* e *Logs*.
   * Feeds completos de tarefas, audiências e filtros de busca.

3. **🔔 Notificações Multicanal por Seções:**
   * **E-mail (SMTP):** Template HTML responsivo organizado por urgência.
   * **Telegram (Bot API):** Mensagens em Markdown com badges e links.
   * **WhatsApp (OpenWA / WPPConnect):** Mensagens diretas via API REST.
   * **Deduplicação Inteligente (SQLite):** Previne envios repetidos no mesmo dia.

4. **🛡️ Operação em Segundo Plano (System Tray):**
   * Minimiza automaticamente para a bandeja ao clicar em fechar (`X`).
   * Menu rápido no ícone da bandeja para abrir o painel, sincronizar ou exibir/ocultar o Fence.

---

## 🚀 Como Iniciar

Dê um duplo clique em:
```cmd
run_app.bat
```
Ou via terminal:
```bash
python -m app.main
```
