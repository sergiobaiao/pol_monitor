import os
import threading
from datetime import datetime
import customtkinter as ctk
from typing import Optional, Dict, Any, List

from ..config import Config
from ..database import Database
from ..pol_client import PolClient
from ..scheduler import Scheduler
from ..notifier.formatter import ClassifiedItems
from .theme import get_windows_accent_color, get_accent_hover

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class MainWindow(ctk.CTkToplevel):
    def __init__(self, master, config: Config, db: Database, pol_client: PolClient, scheduler: Scheduler, on_quit_callback=None, on_logout_callback=None):
        super().__init__(master)

        self.config = config
        self.db = db
        self.pol_client = pol_client
        self.scheduler = scheduler
        self.on_quit_callback = on_quit_callback
        self.on_logout_callback = on_logout_callback
        self.desktop_fence = None

        # Cor de destaque configurada no Windows pelo usuário
        self.accent_color = get_windows_accent_color()
        self.accent_hover = get_accent_hover(self.accent_color)

        self.title("POL ADV — Painel de Prazos & Notificações")
        self.geometry("1100 x 720")
        self.minsize(980, 640)

        # Interceptar fechamento e minimização para a bandeja (conforme configurado)
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self.bind("<Unmap>", self._on_window_unmap)

        self._setup_layout()
        self._setup_sidebar()
        self._setup_views()

        # Callbacks do Scheduler
        self.scheduler.on_sync_complete = self._on_sync_completed_callback
        self.scheduler.on_status_change = self._on_status_changed_callback

        # Carregar primeira view
        self.select_view("widgets")

        # Se houver itens cacheados no SQLite, carregar de imediato
        self._load_cached_data()

    def _get_greeting(self) -> str:
        hour = datetime.now().hour
        if 5 <= hour < 12:
            return "Bom dia"
        elif 12 <= hour < 18:
            return "Boa tarde"
        else:
            return "Boa noite"

    def _setup_layout(self):
        # Grid com 2 colunas: Barra lateral (0) e Conteúdo principal (1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

    def _setup_sidebar(self):
        self.sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0, fg_color="#18181b")
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(7, weight=1)

        # Brand / Logo
        logo_label = ctk.CTkLabel(
            self.sidebar_frame,
            text="⚖️ POL ADV",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=self.accent_color
        )
        logo_label.grid(row=0, column=0, padx=20, pady=(24, 20), sticky="w")

        # Botões de Navegação
        self.nav_buttons = {}
        views = [
            ("widgets", "📊  Widgets / Início"),
            ("tasks", "📋  Tarefas"),
            ("calendar", "📅  Calendário"),
            ("notifications", "🔔  Notificações"),
            ("config", "⚙️  Configurações"),
            ("logs", "📜  Logs & Histórico"),
        ]

        for i, (key, label) in enumerate(views, start=1):
            btn = ctk.CTkButton(
                self.sidebar_frame,
                text=label,
                font=ctk.CTkFont(size=13, weight="normal"),
                anchor="w",
                fg_color="transparent",
                text_color="#d1d5db",
                hover_color="#27272a",
                height=40,
                corner_radius=8,
                command=lambda k=key: self.select_view(k)
            )
            btn.grid(row=i, column=0, padx=12, pady=3, sticky="ew")
            self.nav_buttons[key] = btn

        # Rodapé da sidebar com status do usuário
        user_info = ctk.CTkLabel(
            self.sidebar_frame,
            text=f"{self.config.pol_email}\nEscritório: #{self.config.pol_office_id}\nv1.0.0 (SDD Spec-Kit)",
            font=ctk.CTkFont(size=11),
            text_color="#71717a",
            justify="left"
        )
        user_info.grid(row=8, column=0, padx=20, pady=16, sticky="sw")

    def _setup_views(self):
        # Container principal
        self.main_container = ctk.CTkFrame(self, corner_radius=0, fg_color="#09090b")
        self.main_container.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
        self.main_container.grid_columnconfigure(0, weight=1)
        self.main_container.grid_rowconfigure(1, weight=1)

        # Top Bar Compartilhada
        self.top_bar = ctk.CTkFrame(self.main_container, height=70, fg_color="#18181b", corner_radius=0)
        self.top_bar.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 2))
        self.top_bar.grid_columnconfigure(0, weight=1)

        # Saudação e data
        greeting_text = f"{self._get_greeting()}, Sérgio Baião"
        data_extenso = datetime.now().strftime("%A, %d de %B").capitalize()
        
        greeting_frame = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        greeting_frame.grid(row=0, column=0, padx=24, pady=12, sticky="w")

        self.lbl_greeting = ctk.CTkLabel(
            greeting_frame,
            text=greeting_text,
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#f4f4f5"
        )
        self.lbl_greeting.pack(anchor="w")

        self.lbl_subtitle = ctk.CTkLabel(
            greeting_frame,
            text=f"{data_extenso} • Monitoramento Contínuo",
            font=ctk.CTkFont(size=12),
            text_color="#a1a1aa"
        )
        self.lbl_subtitle.pack(anchor="w")

        # Botão de Sincronizar e Status
        action_frame = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        action_frame.grid(row=0, column=1, padx=24, pady=12, sticky="e")

        self.lbl_status = ctk.CTkLabel(
            action_frame,
            text="🟢 Pronto",
            font=ctk.CTkFont(size=12),
            text_color="#10b981"
        )
        self.lbl_status.pack(side="left", padx=(0, 16))

        self.btn_fence = ctk.CTkButton(
            action_frame,
            text="🖥️ Widget Desktop",
            font=ctk.CTkFont(size=12),
            fg_color="#27272a",
            hover_color="#3f3f46",
            height=34,
            corner_radius=6,
            command=self.toggle_desktop_fence
        )
        self.btn_fence.pack(side="left", padx=(0, 10))

        self.btn_sync = ctk.CTkButton(
            action_frame,
            text="🔄 Sincronizar Agora",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=self.accent_color,
            hover_color=self.accent_hover,
            height=34,
            corner_radius=6,
            command=self.trigger_sync_now
        )
        self.btn_sync.pack(side="left")

        # Frames para cada View
        self.views_frames = {}
        self.views_frames["widgets"] = self._create_widgets_view()
        self.views_frames["tasks"] = self._create_tasks_view()
        self.views_frames["calendar"] = self._create_calendar_view()
        self.views_frames["notifications"] = self._create_notifications_view()
        self.views_frames["config"] = self._create_config_view()
        self.views_frames["logs"] = self._create_logs_view()

    def select_view(self, view_name: str):
        for name, frame in self.views_frames.items():
            if name == view_name:
                frame.grid(row=1, column=0, sticky="nsew", padx=20, pady=20)
                self.nav_buttons[name].configure(fg_color="#27272a", text_color="#ffffff")
            else:
                frame.grid_forget()
                self.nav_buttons[name].configure(fg_color="transparent", text_color="#d1d5db")

    # =========================================================================
    # VIEW 1: WIDGETS (ESTILO WINDOWS 11 WIDGETS PANEL)
    # =========================================================================
    def _create_widgets_view(self) -> ctk.CTkScrollableFrame:
        frame = ctk.CTkScrollableFrame(self.main_container, fg_color="transparent")
        frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        # 1. Quatro Cards de Resumo no Topo
        self.card_today = self._create_metric_card(frame, 0, "🚨 HOJE", "0", "Vencendo hoje", "#ef4444", "#450a0a")
        self.card_tomorrow = self._create_metric_card(frame, 1, "⚠️ PRÓXIMOS 2 DIAS", "0", "Atenção necessária", "#f59e0b", "#451a03")
        self.card_week = self._create_metric_card(frame, 2, "📅 ESTA SEMANA", "0", "Prazos em aberto", "#3b82f6", "#172554")
        self.card_overdue = self._create_metric_card(frame, 3, "⏳ ATRASADAS", "0", "Tarefas pendentes", "#a855f7", "#3b0764")

        # 2. Painéis Principais (Feed em duas colunas)
        feed_container = ctk.CTkFrame(frame, fg_color="transparent")
        feed_container.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=(20, 0))
        feed_container.grid_columnconfigure((0, 1), weight=1)

        # Coluna Esquerda: Tarefas Imediatas
        left_card = ctk.CTkFrame(feed_container, fg_color="#18181b", corner_radius=12)
        left_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        lbl_tasks_title = ctk.CTkLabel(
            left_card,
            text="📋 Prazos e Tarefas Próximas",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#f4f4f5"
        )
        lbl_tasks_title.pack(anchor="w", padx=16, pady=(16, 8))

        self.feed_tasks_box = ctk.CTkScrollableFrame(left_card, fg_color="transparent", height=320)
        self.feed_tasks_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # Coluna Direita: Audiências & Calendário
        right_card = ctk.CTkFrame(feed_container, fg_color="#18181b", corner_radius=12)
        right_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        lbl_cal_title = ctk.CTkLabel(
            right_card,
            text="📅 Próximos Compromissos & Audiências",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#f4f4f5"
        )
        lbl_cal_title.pack(anchor="w", padx=16, pady=(16, 8))

        self.feed_events_box = ctk.CTkScrollableFrame(right_card, fg_color="transparent", height=320)
        self.feed_events_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        return frame

    def _create_metric_card(self, parent, col, title, count, subtitle, accent_color, bg_tint) -> Dict[str, Any]:
        card = ctk.CTkFrame(parent, fg_color="#18181b", corner_radius=12, border_width=1, border_color="#27272a")
        card.grid(row=0, column=col, padx=6, pady=4, sticky="nsew")

        lbl_t = ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=11, weight="bold"), text_color=accent_color)
        lbl_t.pack(anchor="w", padx=14, pady=(12, 2))

        lbl_c = ctk.CTkLabel(card, text=count, font=ctk.CTkFont(size=28, weight="bold"), text_color="#ffffff")
        lbl_c.pack(anchor="w", padx=14, pady=0)

        lbl_s = ctk.CTkLabel(card, text=subtitle, font=ctk.CTkFont(size=11), text_color="#71717a")
        lbl_s.pack(anchor="w", padx=14, pady=(0, 12))

        return {"frame": card, "count_lbl": lbl_c}

    # =========================================================================
    # VIEW 2: TAREFAS DETALHADAS
    # =========================================================================
    def _create_tasks_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.main_container, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        # Filtros no topo
        filter_bar = ctk.CTkFrame(frame, fg_color="transparent")
        filter_bar.grid(row=0, column=0, sticky="ew", pady=(0, 12))

        self.search_task_entry = ctk.CTkEntry(
            filter_bar,
            placeholder_text="🔍 Pesquisar em tarefas ou processos...",
            width=300
        )
        self.search_task_entry.pack(side="left", padx=(0, 10))
        self.search_task_entry.bind("<KeyRelease>", lambda e: self._filter_tasks_view())

        self.seg_task_filter = ctk.CTkSegmentedButton(
            filter_bar,
            values=["Todas", "Hoje", "Próximos Dias", "Semana", "Atrasadas"],
            command=lambda v: self._filter_tasks_view()
        )
        self.seg_task_filter.set("Todas")
        self.seg_task_filter.pack(side="left")

        # Container scrollável com as tarefas
        self.tasks_list_box = ctk.CTkScrollableFrame(frame, fg_color="#18181b", corner_radius=10)
        self.tasks_list_box.grid(row=1, column=0, sticky="nsew")

        return frame

    # =========================================================================
    # VIEW 3: CALENDÁRIO / AUDIÊNCIAS
    # =========================================================================
    def _create_calendar_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.main_container, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        self.cal_list_box = ctk.CTkScrollableFrame(frame, fg_color="#18181b", corner_radius=10)
        self.cal_list_box.grid(row=0, column=0, sticky="nsew")

        return frame

    # =========================================================================
    # VIEW 4: NOTIFICAÇÕES (E-MAIL, TELEGRAM, WHATSAPP)
    # =========================================================================
    def _create_notifications_view(self) -> ctk.CTkScrollableFrame:
        frame = ctk.CTkScrollableFrame(self.main_container, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)

        # 1. E-mail (SMTP)
        box_email = ctk.CTkFrame(frame, fg_color="#18181b", corner_radius=10)
        box_email.pack(fill="x", pady=8, padx=4)

        header_email = ctk.CTkFrame(box_email, fg_color="transparent")
        header_email.pack(fill="x", padx=16, pady=12)

        ctk.CTkLabel(header_email, text="📧 Notificações por E-mail (SMTP)", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self.sw_email = ctk.CTkSwitch(header_email, text="Ativo", font=ctk.CTkFont(size=13))
        if self.config.email_enabled:
            self.sw_email.select()
        self.sw_email.pack(side="right")

        content_email = ctk.CTkFrame(box_email, fg_color="transparent")
        content_email.pack(fill="x", padx=16, pady=(0, 16))
        content_email.grid_columnconfigure((0, 1), weight=1)

        self.ent_smtp_host = self._create_field(content_email, 0, 0, "Servidor SMTP:", self.config.smtp_host)
        self.ent_smtp_port = self._create_field(content_email, 0, 1, "Porta:", str(self.config.smtp_port))
        self.ent_smtp_user = self._create_field(content_email, 1, 0, "Usuário / E-mail Remetente:", self.config.smtp_user)
        self.ent_smtp_pass = self._create_field(content_email, 1, 1, "Senha de Aplicativo:", self.config.smtp_pass, show="*")
        self.ent_smtp_recipients = self._create_field(content_email, 2, 0, "Destinatários (separados por vírgula):", ", ".join(self.config.smtp_recipients))
        
        btn_test_email = ctk.CTkButton(
            content_email, text="🧪 Testar E-mail", width=120, height=32,
            fg_color="#374151", hover_color="#4b5563",
            command=self._test_email
        )
        btn_test_email.grid(row=2, column=1, sticky="se", pady=(18, 0))

        # 2. Telegram
        box_tg = ctk.CTkFrame(frame, fg_color="#18181b", corner_radius=10)
        box_tg.pack(fill="x", pady=8, padx=4)

        header_tg = ctk.CTkFrame(box_tg, fg_color="transparent")
        header_tg.pack(fill="x", padx=16, pady=12)

        ctk.CTkLabel(header_tg, text="✈️ Notificações por Telegram", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self.sw_tg = ctk.CTkSwitch(header_tg, text="Ativo", font=ctk.CTkFont(size=13))
        if self.config.telegram_enabled:
            self.sw_tg.select()
        self.sw_tg.pack(side="right")

        content_tg = ctk.CTkFrame(box_tg, fg_color="transparent")
        content_tg.pack(fill="x", padx=16, pady=(0, 16))
        content_tg.grid_columnconfigure((0, 1), weight=1)

        self.ent_tg_token = self._create_field(content_tg, 0, 0, "Bot Token (obtido via @BotFather):", self.config.telegram_token)
        self.ent_tg_chats = self._create_field(content_tg, 0, 1, "Chat IDs (separados por vírgula):", ", ".join(self.config.telegram_chat_ids))

        btn_test_tg = ctk.CTkButton(
            content_tg, text="🧪 Testar Telegram", width=120, height=32,
            fg_color="#374151", hover_color="#4b5563",
            command=self._test_telegram
        )
        btn_test_tg.grid(row=1, column=1, sticky="se", pady=(10, 0))

        # 3. WhatsApp (OpenWA)
        box_wa = ctk.CTkFrame(frame, fg_color="#18181b", corner_radius=10)
        box_wa.pack(fill="x", pady=8, padx=4)

        header_wa = ctk.CTkFrame(box_wa, fg_color="transparent")
        header_wa.pack(fill="x", padx=16, pady=12)

        ctk.CTkLabel(header_wa, text="💬 Notificações por WhatsApp (OpenWA)", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self.sw_wa = ctk.CTkSwitch(header_wa, text="Ativo", font=ctk.CTkFont(size=13))
        if self.config.openwa_enabled:
            self.sw_wa.select()
        self.sw_wa.pack(side="right")

        content_wa = ctk.CTkFrame(box_wa, fg_color="transparent")
        content_wa.pack(fill="x", padx=16, pady=(0, 16))
        content_wa.grid_columnconfigure((0, 1), weight=1)

        self.ent_wa_url = self._create_field(content_wa, 0, 0, "URL do Servidor OpenWA:", self.config.openwa_url)
        self.ent_wa_key = self._create_field(content_wa, 0, 1, "API Key (Opcional):", self.config.openwa_api_key)
        self.ent_wa_recipients = self._create_field(content_wa, 1, 0, "Números WhatsApp (ex: 5511999999999):", ", ".join(self.config.openwa_recipients))

        btn_test_wa = ctk.CTkButton(
            content_wa, text="🧪 Testar WhatsApp", width=120, height=32,
            fg_color="#374151", hover_color="#4b5563",
            command=self._test_openwa
        )
        btn_test_wa.grid(row=1, column=1, sticky="se", pady=(10, 0))

        # Botão Salvar Todas Notificações
        btn_save_notif = ctk.CTkButton(
            frame, text="💾 Salvar Configurações de Notificação", height=40,
            fg_color="#2563eb", hover_color="#1d4ed8", font=ctk.CTkFont(size=14, weight="bold"),
            command=self._save_notifications_config
        )
        btn_save_notif.pack(fill="x", pady=16, padx=4)

        return frame

    # =========================================================================
    # VIEW 5: CONFIGURAÇÕES POL ADV
    # =========================================================================
    def _create_config_view(self) -> ctk.CTkScrollableFrame:
        frame = ctk.CTkScrollableFrame(self.main_container, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)

        box_pol = ctk.CTkFrame(frame, fg_color="#18181b", corner_radius=10)
        box_pol.pack(fill="x", pady=8, padx=4)

        ctk.CTkLabel(box_pol, text="🔐 Conta POL ADV (office.pol.adv.br)", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=16, pady=(12, 0))
        ctk.CTkLabel(box_pol, text=f"Conectado como {self.config.pol_email}", text_color="#a1a1aa").pack(anchor="w", padx=16, pady=(0, 12))

        content = ctk.CTkFrame(box_pol, fg_color="transparent")
        content.pack(fill="x", padx=16, pady=(0, 16))
        content.grid_columnconfigure((0, 1), weight=1)

        self.ent_pol_office = self._create_field(content, 0, 0, "Office ID (Tenant):", self.config.pol_office_id)
        self.ent_pol_interval = self._create_field(content, 0, 1, "Intervalo de Verificação (minutos):", str(self.config.check_interval_minutes))

        btn_row = ctk.CTkFrame(box_pol, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 16))

        btn_test_login = ctk.CTkButton(
            btn_row, text="🔑 Testar Conexão na API", height=34,
            fg_color="#374151", hover_color="#4b5563",
            command=self._test_pol_login
        )
        btn_test_login.pack(side="left")

        btn_logout = ctk.CTkButton(
            btn_row, text="🚪 Trocar Usuário", height=34,
            fg_color="#374151", hover_color="#4b5563",
            command=lambda: self.on_logout_callback and self.on_logout_callback()
        )
        btn_logout.pack(side="left", padx=8)

        btn_save_pol = ctk.CTkButton(
            btn_row, text="💾 Salvar Credenciais", height=34,
            fg_color=self.accent_color, hover_color=self.accent_hover, font=ctk.CTkFont(weight="bold"),
            command=self._save_pol_config
        )
        btn_save_pol.pack(side="right")

        # -------------------------------------------------------------
        # 2. Frequência & Período de Atualizações
        # -------------------------------------------------------------
        box_interval = ctk.CTkFrame(frame, fg_color="#18181b", corner_radius=10)
        box_interval.pack(fill="x", pady=8, padx=4)

        header_interval = ctk.CTkFrame(box_interval, fg_color="transparent")
        header_interval.pack(fill="x", padx=16, pady=12)

        ctk.CTkLabel(
            header_interval,
            text="⏱️ Período de Atualizações & Sincronização",
            font=ctk.CTkFont(size=15, weight="bold")
        ).pack(side="left")

        curr_interval = self.config.check_interval_minutes
        self.lbl_interval_badge = ctk.CTkLabel(
            header_interval,
            text=f"  Intervalo: {curr_interval} min  ",
            fg_color="#27272a",
            text_color="#f4f4f5",
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=6
        )
        self.lbl_interval_badge.pack(side="right")

        content_interval = ctk.CTkFrame(box_interval, fg_color="transparent")
        content_interval.pack(fill="x", padx=16, pady=(0, 16))

        # Botões de atalho rápido (Presets)
        ctk.CTkLabel(
            content_interval,
            text="Selecione um intervalo pré-definido:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#a1a1aa"
        ).pack(anchor="w", pady=(0, 6))

        presets_frame = ctk.CTkFrame(content_interval, fg_color="transparent")
        presets_frame.pack(fill="x", pady=(0, 10))

        preset_values = [
            ("5 min", 5),
            ("15 min", 15),
            ("30 min", 30),
            ("1 hora", 60),
            ("2 horas", 120),
            ("4 horas", 240),
            ("8 horas", 480)
        ]

        self.preset_buttons = []
        for label, mins in preset_values:
            btn = ctk.CTkButton(
                presets_frame,
                text=label,
                width=65,
                height=28,
                fg_color=self.accent_color if mins == curr_interval else "#27272a",
                hover_color=self.accent_hover,
                font=ctk.CTkFont(size=11),
                command=lambda m=mins: self._select_interval_preset(m)
            )
            btn.pack(side="left", padx=3)
            self.preset_buttons.append((mins, btn))

        # Slider + Campo manual
        slider_row = ctk.CTkFrame(content_interval, fg_color="transparent")
        slider_row.pack(fill="x", pady=(6, 12))
        slider_row.grid_columnconfigure(0, weight=1)

        self.lbl_slider_interval = ctk.CTkLabel(
            slider_row,
            text=f"Ajuste Fino: {curr_interval} minutos ({curr_interval/60:.1f}h)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#a1a1aa"
        )
        self.lbl_slider_interval.grid(row=0, column=0, sticky="w")

        self.slider_interval = ctk.CTkSlider(
            slider_row,
            from_=1,
            to=480,
            number_of_steps=96,
            progress_color=self.accent_color,
            button_color=self.accent_color,
            command=self._on_interval_slider_change
        )
        self.slider_interval.set(curr_interval)
        self.slider_interval.grid(row=1, column=0, sticky="ew", pady=(4, 0), padx=(0, 12))

        # Campo numérico manual
        ent_box = ctk.CTkFrame(slider_row, fg_color="transparent")
        ent_box.grid(row=0, column=1, rowspan=2, sticky="e")
        ctk.CTkLabel(ent_box, text="Minutos:", font=ctk.CTkFont(size=11), text_color="#a1a1aa").pack(anchor="w")
        self.ent_custom_interval = ctk.CTkEntry(ent_box, width=70, height=30)
        self.ent_custom_interval.insert(0, str(curr_interval))
        self.ent_custom_interval.pack()
        self.ent_custom_interval.bind("<KeyRelease>", self._on_interval_entry_change)

        # Horário diário consolidado
        daily_row = ctk.CTkFrame(content_interval, fg_color="transparent")
        daily_row.pack(fill="x", pady=(4, 12))
        ctk.CTkLabel(daily_row, text="Disparo Diário Consolidado (HH:MM):", font=ctk.CTkFont(size=12), text_color="#a1a1aa").pack(side="left")
        self.ent_daily_hour = ctk.CTkEntry(daily_row, width=80, height=30)
        self.ent_daily_hour.insert(0, self.config.notify_daily_hour)
        self.ent_daily_hour.pack(side="left", padx=10)

        # Botões de Ação
        btn_int_row = ctk.CTkFrame(box_interval, fg_color="transparent")
        btn_int_row.pack(fill="x", padx=16, pady=(0, 16))

        btn_sync_now = ctk.CTkButton(
            btn_int_row,
            text="🔄 Sincronizar Agora",
            height=32,
            fg_color="#374151",
            hover_color="#4b5563",
            command=self._manual_sync_trigger
        )
        btn_sync_now.pack(side="left")

        btn_save_interval = ctk.CTkButton(
            btn_int_row,
            text="💾 Salvar Período de Atualização",
            height=32,
            fg_color=self.accent_color,
            hover_color=self.accent_hover,
            font=ctk.CTkFont(weight="bold"),
            command=self._save_interval_config
        )
        btn_save_interval.pack(side="right")

        # -------------------------------------------------------------
        # 3. Personalização do Widget Desktop (Tamanho e Transparência)
        # -------------------------------------------------------------
        box_fence = ctk.CTkFrame(frame, fg_color="#18181b", corner_radius=10)
        box_fence.pack(fill="x", pady=8, padx=4)

        header_fence = ctk.CTkFrame(box_fence, fg_color="transparent")
        header_fence.pack(fill="x", padx=16, pady=12)

        ctk.CTkLabel(
            header_fence,
            text="🎨 Personalização do Widget Desktop (Fences)",
            font=ctk.CTkFont(size=15, weight="bold")
        ).pack(side="left")

        # Indicador da cor do Windows
        accent_pill = ctk.CTkLabel(
            header_fence,
            text=f"  Cor do Windows: {self.accent_color}  ",
            fg_color=self.accent_color,
            text_color="#ffffff",
            font=ctk.CTkFont(size=11, weight="bold"),
            corner_radius=6
        )
        accent_pill.pack(side="right")

        content_fence = ctk.CTkFrame(box_fence, fg_color="transparent")
        content_fence.pack(fill="x", padx=16, pady=(0, 16))
        content_fence.grid_columnconfigure((0, 1), weight=1)

        # Slider de Largura
        init_w = int(self.db.get_setting("desktop_fence_w", "360"))
        row_w = ctk.CTkFrame(content_fence, fg_color="transparent")
        row_w.grid(row=0, column=0, padx=8, pady=6, sticky="ew")
        self.lbl_slider_w = ctk.CTkLabel(row_w, text=f"Largura: {init_w} px", font=ctk.CTkFont(size=12, weight="bold"), text_color="#a1a1aa")
        self.lbl_slider_w.pack(anchor="w")
        self.slider_w = ctk.CTkSlider(row_w, from_=260, to=700, number_of_steps=44, progress_color=self.accent_color, button_color=self.accent_color, command=self._on_width_change)
        self.slider_w.set(init_w)
        self.slider_w.pack(fill="x", pady=(4, 0))

        # Slider de Altura
        init_h = int(self.db.get_setting("desktop_fence_h", "600"))
        row_h = ctk.CTkFrame(content_fence, fg_color="transparent")
        row_h.grid(row=0, column=1, padx=8, pady=6, sticky="ew")
        self.lbl_slider_h = ctk.CTkLabel(row_h, text=f"Altura: {init_h} px", font=ctk.CTkFont(size=12, weight="bold"), text_color="#a1a1aa")
        self.lbl_slider_h.pack(anchor="w")
        self.slider_h = ctk.CTkSlider(row_h, from_=300, to=900, number_of_steps=60, progress_color=self.accent_color, button_color=self.accent_color, command=self._on_height_change)
        self.slider_h.set(init_h)
        self.slider_h.pack(fill="x", pady=(4, 0))

        # Slider de Transparência / Opacidade
        init_alpha = int(float(self.db.get_setting("desktop_fence_alpha", "0.90")) * 100)
        row_alpha = ctk.CTkFrame(content_fence, fg_color="transparent")
        row_alpha.grid(row=1, column=0, columnspan=2, padx=8, pady=6, sticky="ew")
        self.lbl_slider_alpha = ctk.CTkLabel(row_alpha, text=f"Opacidade / Transparência: {init_alpha}%", font=ctk.CTkFont(size=12, weight="bold"), text_color="#a1a1aa")
        self.lbl_slider_alpha.pack(anchor="w")
        self.slider_alpha = ctk.CTkSlider(row_alpha, from_=25, to=100, number_of_steps=75, progress_color=self.accent_color, button_color=self.accent_color, command=self._on_alpha_change)
        self.slider_alpha.set(init_alpha)
        self.slider_alpha.pack(fill="x", pady=(4, 0))

        # Botões de ação do Fence
        btn_fence_row = ctk.CTkFrame(box_fence, fg_color="transparent")
        btn_fence_row.pack(fill="x", padx=16, pady=(0, 16))

        btn_reset_fence = ctk.CTkButton(
            btn_fence_row, text="↺ Padrões (360x600, 90%)", height=32,
            fg_color="#374151", hover_color="#4b5563",
            command=self._reset_fence_defaults
        )
        btn_reset_fence.pack(side="left")

        btn_save_fence = ctk.CTkButton(
            btn_fence_row, text="💾 Salvar Dimensões e Opacidade", height=32,
            fg_color=self.accent_color, hover_color=self.accent_hover, font=ctk.CTkFont(weight="bold"),
            command=self._save_fence_settings
        )
        btn_save_fence.pack(side="right")

        # -------------------------------------------------------------
        # 3. Opções de Inicialização e Comportamento da Janela
        # -------------------------------------------------------------
        box_startup = ctk.CTkFrame(frame, fg_color="#18181b", corner_radius=10)
        box_startup.pack(fill="x", pady=8, padx=4)

        ctk.CTkLabel(
            box_startup,
            text="🚀 Inicialização e Comportamento da Janela",
            font=ctk.CTkFont(size=15, weight="bold")
        ).pack(anchor="w", padx=16, pady=12)

        content_startup = ctk.CTkFrame(box_startup, fg_color="transparent")
        content_startup.pack(fill="x", padx=16, pady=(0, 16))

        # Switch 1: Exibir widget no desktop ao iniciar
        self.sw_startup_fence = ctk.CTkSwitch(
            content_startup,
            text="Exibir widget na Área de Trabalho ao iniciar (estilo Fences)",
            font=ctk.CTkFont(size=13),
            progress_color=self.accent_color
        )
        if self.config.desktop_fence_enabled:
            self.sw_startup_fence.select()
        else:
            self.sw_startup_fence.deselect()
        self.sw_startup_fence.pack(anchor="w", pady=6)

        # Switch 2: Abrir janela principal ao iniciar
        self.sw_startup_main = ctk.CTkSwitch(
            content_startup,
            text="Abrir painel principal automaticamente ao iniciar (se desativado, inicia silencioso na bandeja)",
            font=ctk.CTkFont(size=13),
            progress_color=self.accent_color
        )
        if not self.config.start_minimized:
            self.sw_startup_main.select()
        else:
            self.sw_startup_main.deselect()
        self.sw_startup_main.pack(anchor="w", pady=6)

        # Switch 3: Minimizar para a bandeja ao clicar em '-'
        self.sw_minimize_to_tray = ctk.CTkSwitch(
            content_startup,
            text="Minimizar para a bandeja do sistema ao clicar em '_' (oculta da barra de tarefas)",
            font=ctk.CTkFont(size=13),
            progress_color=self.accent_color
        )
        if self.config.minimize_to_tray:
            self.sw_minimize_to_tray.select()
        else:
            self.sw_minimize_to_tray.deselect()
        self.sw_minimize_to_tray.pack(anchor="w", pady=6)

        # Switch 4: Fechar para a bandeja ao clicar em 'X'
        self.sw_close_to_tray = ctk.CTkSwitch(
            content_startup,
            text="Fechar para a bandeja do sistema ao clicar no 'X' (em vez de encerrar o app)",
            font=ctk.CTkFont(size=13),
            progress_color=self.accent_color
        )
        if self.config.close_to_tray:
            self.sw_close_to_tray.select()
        else:
            self.sw_close_to_tray.deselect()
        self.sw_close_to_tray.pack(anchor="w", pady=6)

        btn_save_startup = ctk.CTkButton(
            box_startup,
            text="💾 Salvar Opções de Janela e Inicialização",
            height=32,
            fg_color=self.accent_color,
            hover_color=self.accent_hover,
            font=ctk.CTkFont(weight="bold"),
            command=self._save_startup_config
        )
        btn_save_startup.pack(anchor="e", padx=16, pady=(0, 16))

        return frame

    # =========================================================================
    # VIEW 6: LOGS & HISTÓRICO
    # =========================================================================
    def _create_logs_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.main_container, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        top_row = ctk.CTkFrame(frame, fg_color="transparent")
        top_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        ctk.CTkLabel(top_row, text="📜 Histórico de Notificações e Auditoria", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")

        ctk.CTkButton(top_row, text="🔄 Recarregar", width=90, height=28, command=self._refresh_logs_view).pack(side="right", padx=4)
        ctk.CTkButton(top_row, text="🗑️ Limpar", width=80, height=28, fg_color="#b91c1c", hover_color="#991b1b", command=self._clear_logs).pack(side="right", padx=4)

        self.logs_textbox = ctk.CTkTextbox(frame, fg_color="#18181b", font=ctk.CTkFont(family="Consolas", size=11))
        self.logs_textbox.grid(row=1, column=0, sticky="nsew")

        return frame

    # =========================================================================
    # UTILITÁRIOS E AÇÕES
    # =========================================================================
    def _create_field(self, parent, r, c, label, value, show=None):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=r, column=c, padx=8, pady=6, sticky="ew")
        ctk.CTkLabel(frame, text=label, font=ctk.CTkFont(size=12, weight="bold"), text_color="#a1a1aa").pack(anchor="w")
        ent = ctk.CTkEntry(frame, height=32, show=show)
        ent.insert(0, value)
        ent.pack(fill="x", pady=(2, 0))
        return ent

    def _load_cached_data(self):
        items = self.db.get_cached_items()
        tasks = [json_to_dict(it["raw_json"]) for it in items if it["item_type"] == "TASK"]
        events = [json_to_dict(it["raw_json"]) for it in items if it["item_type"] == "EVENT"]
        if tasks or events:
            from ..notifier.formatter import NotificationFormatter
            classified = NotificationFormatter.classify_items(tasks, events)
            self._update_ui_with_data(classified)

    def trigger_sync_now(self):
        self.btn_sync.configure(state="disabled", text="⏳ Sincronizando...")
        self.lbl_status.configure(text="🟡 Sincronizando...", text_color="#f59e0b")

        def run():
            classified = self.scheduler.sync_and_notify(force=True)
            self.after(0, lambda: self._on_sync_finished(classified))

        threading.Thread(target=run, daemon=True).start()

    def _on_sync_finished(self, classified: ClassifiedItems):
        self.btn_sync.configure(state="normal", text="🔄 Sincronizar Agora")
        self.lbl_status.configure(text=f"🟢 Atualizado {datetime.now().strftime('%H:%M')}", text_color="#10b981")
        self._update_ui_with_data(classified)

    def _on_sync_completed_callback(self, classified: ClassifiedItems):
        self.after(0, lambda: self._update_ui_with_data(classified))

    def _on_status_changed_callback(self, status: str):
        self.after(0, lambda: self.lbl_status.configure(text=status))

    def _update_ui_with_data(self, classified: ClassifiedItems):
        # Atualizar Cards do Dashboard
        self.card_today["count_lbl"].configure(text=str(len(classified.today)))
        self.card_tomorrow["count_lbl"].configure(text=str(len(classified.tomorrow)))
        self.card_week["count_lbl"].configure(text=str(len(classified.this_week)))
        self.card_overdue["count_lbl"].configure(text=str(len(classified.overdue)))

        # Atualizar Feed de Tarefas
        for child in self.feed_tasks_box.winfo_children():
            child.destroy()

        active_tasks = classified.today + classified.tomorrow + classified.this_week
        if not active_tasks:
            ctk.CTkLabel(self.feed_tasks_box, text="Nenhum prazo pendente para os próximos dias.", text_color="#71717a").pack(pady=20)
        else:
            for it in active_tasks:
                self._render_card_item(self.feed_tasks_box, it)

        # Atualizar Feed de Eventos
        for child in self.feed_events_box.winfo_children():
            child.destroy()

        all_events = [it for it in (classified.today + classified.tomorrow + classified.this_week) if it["type"] != "Tarefa"]
        if not all_events:
            ctk.CTkLabel(self.feed_events_box, text="Nenhuma audiência marcada para esta semana.", text_color="#71717a").pack(pady=20)
        else:
            for ev in all_events:
                self._render_card_item(self.feed_events_box, ev, is_event=True)

        self._filter_tasks_view()
        self._render_calendar_view()
        self._refresh_logs_view()

    def _render_card_item(self, parent, item: Dict[str, Any], is_event: bool = False):
        card = ctk.CTkFrame(parent, fg_color="#27272a", corner_radius=8)
        card.pack(fill="x", pady=4, padx=4)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 2))

        badge_color = "#3b82f6" if is_event else "#ef4444" if "HOJE" in item.get("deadline_str", "") else "#eab308"
        badge = ctk.CTkLabel(
            top, text=f" {item['type']} ", fg_color=badge_color,
            text_color="#ffffff", font=ctk.CTkFont(size=10, weight="bold"), corner_radius=4
        )
        badge.pack(side="left")

        dt_lbl = ctk.CTkLabel(top, text=item["deadline_str"], font=ctk.CTkFont(size=11), text_color="#a1a1aa")
        dt_lbl.pack(side="right")

        title_lbl = ctk.CTkLabel(
            card, text=item["title"], font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#f4f4f5", anchor="w", justify="left", wraplength=400
        )
        title_lbl.pack(fill="x", padx=10, pady=(0, 6))

    def _filter_tasks_view(self):
        for child in self.tasks_list_box.winfo_children():
            child.destroy()

        classified = self.scheduler.last_classified
        if not classified:
            return

        cat = self.seg_task_filter.get()
        query = self.search_task_entry.get().lower().strip()

        if cat == "Hoje":
            items = classified.today
        elif cat == "Próximos Dias":
            items = classified.tomorrow
        elif cat == "Semana":
            items = classified.this_week
        elif cat == "Atrasadas":
            items = classified.overdue
        else:
            items = classified.today + classified.tomorrow + classified.this_week + classified.overdue

        # Apenas tarefas
        items = [it for it in items if it["type"] == "Tarefa"]

        if query:
            items = [it for it in items if query in it["title"].lower() or query in it["description"].lower()]

        if not items:
            ctk.CTkLabel(self.tasks_list_box, text="Nenhuma tarefa corresponde aos filtros selecionados.", text_color="#71717a").pack(pady=40)
            return

        for it in items:
            self._render_detailed_task_row(self.tasks_list_box, it)

    def _render_detailed_task_row(self, parent, it: Dict[str, Any]):
        row = ctk.CTkFrame(parent, fg_color="#27272a", corner_radius=8)
        row.pack(fill="x", pady=4, padx=6)

        header = ctk.CTkFrame(row, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 4))

        ctk.CTkLabel(header, text=it["title"], font=ctk.CTkFont(size=13, weight="bold"), text_color="#f4f4f5", anchor="w").pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(header, text=f"📅 {it['deadline_str']}", font=ctk.CTkFont(size=12, weight="bold"), text_color="#60a5fa").pack(side="right")

        if it["description"]:
            ctk.CTkLabel(row, text=it["description"], font=ctk.CTkFont(size=11), text_color="#9ca3af", anchor="w", justify="left").pack(fill="x", padx=12, pady=(0, 10))

    def _render_calendar_view(self):
        for child in self.cal_list_box.winfo_children():
            child.destroy()

        classified = self.scheduler.last_classified
        if not classified:
            return

        events = [it for it in (classified.today + classified.tomorrow + classified.this_week) if it["type"] != "Tarefa"]

        if not events:
            ctk.CTkLabel(self.cal_list_box, text="Nenhum compromisso ou audiência agendada.", text_color="#71717a").pack(pady=40)
            return

        for ev in events:
            row = ctk.CTkFrame(self.cal_list_box, fg_color="#27272a", corner_radius=8)
            row.pack(fill="x", pady=6, padx=8)

            head = ctk.CTkFrame(row, fg_color="transparent")
            head.pack(fill="x", padx=12, pady=(10, 4))

            ctk.CTkLabel(head, text=f"⚖️ {ev['title']}", font=ctk.CTkFont(size=14, weight="bold"), text_color="#f4f4f5").pack(side="left")
            ctk.CTkLabel(head, text=f"🕒 {ev['deadline_str']}", font=ctk.CTkFont(size=12, weight="bold"), text_color="#34d399").pack(side="right")

            if ev["description"]:
                ctk.CTkLabel(row, text=f"Local/Pauta: {ev['description']}", font=ctk.CTkFont(size=12), text_color="#d1d5db").pack(anchor="w", padx=12, pady=(0, 10))

    def _refresh_logs_view(self):
        self.logs_textbox.delete("1.0", "end")
        logs = self.db.get_logs(limit=100)
        for log in logs:
            self.logs_textbox.insert("end", f"[{log['timestamp']}] [{log['level']}] {log['message']}\n")

    def _clear_logs(self):
        self.db.clear_logs()
        self._refresh_logs_view()

    # --- Testes e Saves ---
    def _test_pol_login(self):
        self.db.log("INFO", f"Testando autenticação para {self.config.pol_email}...")
        success = self.pol_client.authenticate()
        if success:
            self.db.log("INFO", "Autenticação POL ADV validada com sucesso!")
            self._show_alert("Sucesso", "Autenticação na API do POL ADV realizada com sucesso!")
        else:
            self._show_alert("Erro", "Falha na autenticação. Use \"Trocar Usuário\" para entrar novamente.")

    def _save_pol_config(self):
        self.config.pol_office_id = self.ent_pol_office.get()
        if hasattr(self, "ent_pol_interval") and self.ent_pol_interval.get():
            try:
                mins = max(1, int(self.ent_pol_interval.get()))
                self.config.check_interval_minutes = mins
                if hasattr(self, "slider_interval"):
                    self.slider_interval.set(min(480, mins))
                    self._update_interval_display(mins)
                self.scheduler.restart_cycle()
            except ValueError:
                pass
        self.db.log("INFO", "Configurações POL salvas com sucesso.")
        self._show_alert("Sucesso", "Configurações POL salvas com sucesso!")

    def _select_interval_preset(self, mins: int):
        self.slider_interval.set(mins)
        self.ent_custom_interval.delete(0, "end")
        self.ent_custom_interval.insert(0, str(mins))
        self._update_interval_display(mins)

    def _on_interval_slider_change(self, val):
        mins = int(val)
        self.ent_custom_interval.delete(0, "end")
        self.ent_custom_interval.insert(0, str(mins))
        self._update_interval_display(mins)

    def _on_interval_entry_change(self, event=None):
        try:
            val = int(self.ent_custom_interval.get())
            if 1 <= val <= 1440:
                self.slider_interval.set(min(480, val))
                self._update_interval_display(val)
        except ValueError:
            pass

    def _update_interval_display(self, mins: int):
        hours = mins / 60.0
        text = f"Ajuste Fino: {mins} minuto{'s' if mins != 1 else ''}"
        if mins >= 60:
            text += f" ({hours:.1f}h)"
        self.lbl_slider_interval.configure(text=text)
        self.lbl_interval_badge.configure(text=f"  Intervalo: {mins} min  ")

        # Atualiza cores dos botões de preset
        for p_mins, btn in getattr(self, "preset_buttons", []):
            if p_mins == mins:
                btn.configure(fg_color=self.accent_color)
            else:
                btn.configure(fg_color="#27272a")

    def _save_interval_config(self):
        try:
            mins = int(self.ent_custom_interval.get() or "60")
            mins = max(1, min(1440, mins))
        except ValueError:
            mins = 60

        daily_hour = self.ent_daily_hour.get().strip() or "08:00"

        self.config.check_interval_minutes = mins
        self.config.notify_daily_hour = daily_hour
        if hasattr(self, "ent_pol_interval"):
            self.ent_pol_interval.delete(0, "end")
            self.ent_pol_interval.insert(0, str(mins))

        # Acordar o agendador imediatamente para o novo ciclo
        self.scheduler.restart_cycle()

        self.db.log("INFO", f"Período de atualização reconfigurado para {mins} minutos (disparo diário às {daily_hour}).")
        self._show_alert("Sucesso", f"Período de atualização salvo com sucesso!\nO agendador agora verifica a cada {mins} minutos.")

    def _manual_sync_trigger(self):
        import threading
        self._show_alert("Sincronização", "Sincronização iniciada em segundo plano.")
        threading.Thread(target=lambda: self.scheduler.sync_and_notify(force=True), daemon=True).start()

    def _on_width_change(self, val):
        w = int(val)
        self.lbl_slider_w.configure(text=f"Largura: {w} px")
        if self.desktop_fence:
            self.desktop_fence.set_dimensions(w, self.desktop_fence.current_h)

    def _on_height_change(self, val):
        h = int(val)
        self.lbl_slider_h.configure(text=f"Altura: {h} px")
        if self.desktop_fence:
            self.desktop_fence.set_dimensions(self.desktop_fence.current_w, h)

    def _on_alpha_change(self, val):
        alpha_pct = int(val)
        self.lbl_slider_alpha.configure(text=f"Opacidade / Transparência: {alpha_pct}%")
        if self.desktop_fence:
            self.desktop_fence.set_opacity(alpha_pct / 100.0)

    def _reset_fence_defaults(self):
        self.slider_w.set(360)
        self.slider_h.set(600)
        self.slider_alpha.set(90)
        self._on_width_change(360)
        self._on_height_change(600)
        self._on_alpha_change(90)
        self._save_fence_settings()

    def _save_fence_settings(self):
        w = int(self.slider_w.get())
        h = int(self.slider_h.get())
        alpha = float(self.slider_alpha.get()) / 100.0
        self.db.set_setting("desktop_fence_w", str(w))
        self.db.set_setting("desktop_fence_h", str(h))
        self.db.set_setting("desktop_fence_alpha", f"{alpha:.2f}")
        self._show_alert("Sucesso", "Dimensões e transparência salvas com sucesso!")

    def _save_startup_config(self):
        show_fence = (self.sw_startup_fence.get() == 1)
        show_main = (self.sw_startup_main.get() == 1)
        min_to_tray = (self.sw_minimize_to_tray.get() == 1)
        close_to_tray = (self.sw_close_to_tray.get() == 1)

        self.config.desktop_fence_enabled = show_fence
        self.config.start_minimized = not show_main
        self.config.minimize_to_tray = min_to_tray
        self.config.close_to_tray = close_to_tray

        self.db.log("INFO", f"Opções de janela e inicialização salvas: Fence={show_fence}, Painel={show_main}, MinTray={min_to_tray}, CloseTray={close_to_tray}")
        self._show_alert("Sucesso", "Opções de janela e inicialização salvas com sucesso!")

    def _save_notifications_config(self):
        self.config.email_enabled = self.sw_email.get() == 1
        self.config.smtp_host = self.ent_smtp_host.get()
        self.config.smtp_port = int(self.ent_smtp_port.get() or "587")
        self.config.smtp_user = self.ent_smtp_user.get()
        self.config.smtp_pass = self.ent_smtp_pass.get()
        self.config.smtp_recipients = self.ent_smtp_recipients.get()

        self.config.telegram_enabled = self.sw_tg.get() == 1
        self.config.telegram_token = self.ent_tg_token.get()
        self.config.telegram_chat_ids = self.ent_tg_chats.get()

        self.config.openwa_enabled = self.sw_wa.get() == 1
        self.config.openwa_url = self.ent_wa_url.get()
        self.config.openwa_api_key = self.ent_wa_key.get()
        self.config.openwa_recipients = self.ent_wa_recipients.get()

        self.db.log("INFO", "Canais de notificação atualizados.")
        self._show_alert("Sucesso", "Configurações de notificação salvas com sucesso!")

    def _test_email(self):
        recipients = [r.strip() for r in self.ent_smtp_recipients.get().split(",") if r.strip()]
        if not recipients:
            self._show_alert("Atenção", "Preencha ao menos um destinatário de e-mail.")
            return

        from ..notifier.email_service import EmailService
        svc = EmailService(self.config, self.db)
        success, msg = svc.send_notification("🧪 Teste de Notificação POL ADV", "<p>Este é um e-mail de teste do POL ADV Desktop Notifier.</p>", recipients)
        self._show_alert("Resultado do Teste", msg)

    def _test_telegram(self):
        chats = [c.strip() for c in self.ent_tg_chats.get().split(",") if c.strip()]
        if not chats:
            self._show_alert("Atenção", "Preencha ao menos um Chat ID do Telegram.")
            return

        from ..notifier.telegram_service import TelegramService
        svc = TelegramService(self.config, self.db)
        success, msg = svc.send_notification("🧪 *Teste do POL ADV Desktop Notifier*\nMensagem de teste enviada com sucesso!", chats)
        self._show_alert("Resultado do Teste", msg)

    def _test_openwa(self):
        recipients = [w.strip() for w in self.ent_wa_recipients.get().split(",") if w.strip()]
        if not recipients:
            self._show_alert("Atenção", "Preencha ao menos um número de WhatsApp.")
            return

        from ..notifier.openwa_service import OpenWaService
        svc = OpenWaService(self.config, self.db)
        success, msg = svc.send_notification("*⚖️ POL ADV Desktop Notifier*\nTeste de envio via WhatsApp concluído!", recipients)
        self._show_alert("Resultado do Teste", msg)

    def _show_alert(self, title: str, message: str, is_error: bool = False):
        ModernAlert(self, title, message, is_error=is_error)

    def _on_window_close(self):
        if self.config.close_to_tray:
            self.hide_to_tray()
        else:
            if self.on_quit_callback:
                self.on_quit_callback()
            else:
                self.destroy()

    def _on_window_unmap(self, event):
        # Quando o usuário clica em minimizar '_' no Windows e a opção está habilitada
        if event.widget == self and self.config.minimize_to_tray and self.state() == "iconic":
            self.after(10, self.withdraw)

    def hide_to_tray(self):
        self.withdraw()

    def show_from_tray(self):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    def set_desktop_fence(self, fence):
        self.desktop_fence = fence
        if fence and hasattr(self, "btn_fence"):
            is_open = fence.winfo_viewable()
            self.btn_fence.configure(fg_color="#1e3a8a" if is_open else "#27272a")

    def toggle_desktop_fence(self):
        if self.desktop_fence:
            if self.desktop_fence.winfo_viewable():
                self.desktop_fence.hide_widget()
                self.btn_fence.configure(fg_color="#27272a")
            else:
                self.desktop_fence.show_widget()
                self.btn_fence.configure(fg_color="#1e3a8a")

class ModernAlert(ctk.CTkToplevel):
    def __init__(self, parent, title: str, message: str, is_error: bool = False):
        super().__init__(parent)
        self.title(title)
        self.geometry("440x210")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        # Centralizar na janela pai
        self.update_idletasks()
        try:
            x = parent.winfo_x() + (parent.winfo_width() // 2) - 220
            y = parent.winfo_y() + (parent.winfo_height() // 2) - 105
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        is_success = "sucesso" in title.lower() or "sucesso" in message.lower()
        if is_error or "erro" in title.lower() or "falha" in message.lower():
            icon_symbol = "❌"
            theme_color = "#ef4444"
            btn_hover = "#dc2626"
        elif is_success:
            icon_symbol = "✅"
            theme_color = "#10b981"
            btn_hover = "#059669"
        else:
            icon_symbol = "ℹ️"
            theme_color = "#3b82f6"
            btn_hover = "#2563eb"

        frame = ctk.CTkFrame(self, fg_color="#18181b", corner_radius=12)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 6))

        ctk.CTkLabel(
            header,
            text=f"{icon_symbol} {title}",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=theme_color
        ).pack(side="left")

        ctk.CTkLabel(
            frame,
            text=message,
            font=ctk.CTkFont(size=12),
            text_color="#e4e4e7",
            wraplength=380,
            justify="left"
        ).pack(fill="x", padx=16, pady=(0, 16))

        btn = ctk.CTkButton(
            frame,
            text="Entendido",
            width=100,
            height=32,
            fg_color=theme_color,
            hover_color=btn_hover,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self.destroy
        )
        btn.pack(anchor="e", padx=16, pady=(0, 12))

def json_to_dict(val: str) -> Dict[str, Any]:
    import json
    try:
        return json.loads(val)
    except Exception:
        return {}
