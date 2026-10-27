import ctypes
import ctypes.wintypes
import threading
from datetime import datetime
from typing import Optional, Dict, Any, List
import customtkinter as ctk

from ..config import Config
from ..database import Database
from ..scheduler import Scheduler
from ..notifier.formatter import ClassifiedItems
from .theme import get_windows_accent_color, get_accent_hover

# Constantes Win32
HWND_BOTTOM = 1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
GWL_EXSTYLE = -20
GWLP_HWNDPARENT = -8
GW_OWNER = 4
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

user32 = ctypes.windll.user32
user32.SetWindowLongPtrW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t

def set_owner_async(hwnd: int, owner_hwnd: int):
    """Define o Progman como owner (não pai) do widget.

    Por que não SetParent: no Windows 11 24H2+ o Progman tem WS_EX_NOREDIRECTIONBITMAP, e uma
    janela filha vinda de outro processo nunca é composta (fica invisível ou mostra lixo). O Fences
    só escapa disso porque roda dentro do explorer.exe (DesktopDock64.dll injetada). Uma janela
    top-level cujo owner é o Progman continua sendo desenhada normalmente (inclusive com -alpha),
    fica acima do desktop e não é minimizada pelo Win + D.

    Roda em outra thread: chamadas Win32 que enviam mensagens síncronas ao WndProc do Tk, feitas
    da thread do Tk via ctypes, derrubam o Python 3.14 com `PyEval_RestoreThread`.
    """
    def run():
        user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, owner_hwnd)
        user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    threading.Thread(target=run, daemon=True).start()

class DesktopFenceWidget(ctk.CTk):
    def __init__(self, config: Config, db: Database, scheduler: Scheduler):
        super().__init__()

        self.config = config
        self.db = db
        self.scheduler = scheduler
        self._desktop_container_hwnd = 0
        self.main_window = None

        # Status de ativação pelo usuário
        self._is_enabled = (self.db.get_setting("desktop_fence_enabled", "true") == "true")

        # Cor de destaque do Windows (usada apenas nos textos e botões internos, sem borda externa)
        self.accent_color = get_windows_accent_color()
        self.accent_hover = get_accent_hover(self.accent_color)

        # Sem bordas nativas e com transparência configurada
        self.overrideredirect(True)
        self.configure(fg_color="#18181b")

        alpha = float(self.db.get_setting("desktop_fence_alpha", "0.90"))
        self.attributes("-alpha", max(0.2, min(1.0, alpha)))

        # Carregar dimensões salvas
        self.current_w = int(self.db.get_setting("desktop_fence_w", "360"))
        self.current_h = int(self.db.get_setting("desktop_fence_h", "600"))

        # Carregar coordenadas exatas salvas pelo usuário (sem clamp de tela para preservar multi-monitor)
        saved_x = self.db.get_setting("desktop_fence_x")
        saved_y = self.db.get_setting("desktop_fence_y")

        if saved_x is not None and saved_y is not None:
            x = int(saved_x)
            y = int(saved_y)
        else:
            screen_w = self.winfo_screenwidth()
            x = max(20, screen_w - self.current_w - 40)
            y = 100

        self.geometry(f"{self.current_w}x{self.current_h}+{x}+{y}")

        # Estado de arraste e redimensionamento
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._resize_start_x = 0
        self._resize_start_y = 0
        self._orig_w = self.current_w
        self._orig_h = self.current_h

        self._build_ui()
        self.update_data(self.scheduler.last_classified)

        # Forçar renderização do Win32 HWND antes do pin
        self.update_idletasks()
        self.update()

        # Win + D é tratado pelo owner = Progman. Não restaurar (deiconify) a janela por conta própria:
        # o Windows vê isso como janela restaurada e o segundo Win + D deixa de devolver as janelas abertas.
        self.bind("<FocusOut>", lambda e: self.after(30, self.send_to_desktop_layer))

        # Fixar no desktop container (estilo Stardock Fences)
        self.pin_to_desktop()
        self.after(100, self.pin_to_desktop)

        # Monitoramento contínuo para manter fixado ao Desktop
        self._maintain_desktop_pin()

    def _get_target_hwnd(self) -> int:
        user32 = ctypes.windll.user32
        hwnd = self.winfo_id()
        parent = user32.GetParent(hwnd)
        return parent if parent else hwnd

    def pin_to_desktop(self):
        """Prende o widget à camada do Desktop tornando o Progman seu owner (ver set_owner_async)."""
        if not self._is_enabled:
            return
        try:
            user32 = ctypes.windll.user32
            target_hwnd = self._get_target_hwnd()
            progman = user32.FindWindowW("Progman", None)
            if not target_hwnd or not progman:
                return

            self._desktop_container_hwnd = progman

            # Estilo ToolWindow para não aparecer no Alt+Tab nem na barra de tarefas
            style = user32.GetWindowLongW(target_hwnd, GWL_EXSTYLE)
            style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
            user32.SetWindowLongW(target_hwnd, GWL_EXSTYLE, style)

            if user32.GetWindow(target_hwnd, GW_OWNER) != progman:
                set_owner_async(target_hwnd, progman)
            else:
                self.send_to_desktop_layer()
        except Exception:
            pass

    def send_to_desktop_layer(self):
        """Envia o widget para a base da Z-order (logo acima do desktop, atrás das outras janelas)."""
        if not self._is_enabled:
            return
        try:
            user32 = ctypes.windll.user32
            target_hwnd = self._get_target_hwnd()
            user32.SetWindowPos(target_hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception:
            pass

    def _maintain_desktop_pin(self):
        """Loop permanente que garante que o widget permaneça preso ao Desktop mesmo se o Explorer reiniciar."""
        if self.winfo_exists():
            if self._is_enabled:
                user32 = ctypes.windll.user32
                target_hwnd = self._get_target_hwnd()
                if not self._desktop_container_hwnd or not user32.IsWindow(self._desktop_container_hwnd):
                    self.pin_to_desktop()
                elif user32.GetWindow(target_hwnd, GW_OWNER) != self._desktop_container_hwnd:
                    self.pin_to_desktop()
                else:
                    self.send_to_desktop_layer()
            self.after(1000, self._maintain_desktop_pin)

    def set_dimensions(self, w: int, h: int):
        self.current_w = max(260, min(1000, w))
        self.current_h = max(300, min(1400, h))
        self.geometry(f"{self.current_w}x{self.current_h}+{self.winfo_x()}+{self.winfo_y()}")
        self.db.set_setting("desktop_fence_w", str(self.current_w))
        self.db.set_setting("desktop_fence_h", str(self.current_h))

    def set_opacity(self, alpha: float):
        clean_alpha = max(0.2, min(1.0, alpha))
        self.attributes("-alpha", clean_alpha)
        self.db.set_setting("desktop_fence_alpha", f"{clean_alpha:.2f}")

    def _build_ui(self):
        # Container principal SEM BORDA EXTERNA (border_width=0 e padx=0)
        self.main_frame = ctk.CTkFrame(
            self,
            fg_color="#18181b",
            border_width=0,
            corner_radius=12
        )
        self.main_frame.pack(fill="both", expand=True, padx=0, pady=0)

        # Cabeçalho arrastável (SEM botões de fechar ou minimizar)
        self.header_frame = ctk.CTkFrame(
            self.main_frame,
            height=38,
            fg_color="#202024",
            corner_radius=10
        )
        self.header_frame.pack(fill="x", padx=6, pady=(6, 4))
        self.header_frame.pack_propagate(False)

        self.header_frame.bind("<ButtonPress-1>", self._start_drag)
        self.header_frame.bind("<B1-Motion>", self._on_drag)
        self.header_frame.bind("<ButtonRelease-1>", self._finish_drag)

        title_lbl = ctk.CTkLabel(
            self.header_frame,
            text="⚖️ Prazos & Tarefas POL",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#f4f4f5"
        )
        title_lbl.pack(side="left", padx=10)
        title_lbl.bind("<ButtonPress-1>", self._start_drag)
        title_lbl.bind("<B1-Motion>", self._on_drag)
        title_lbl.bind("<ButtonRelease-1>", self._finish_drag)

        # Botão de sincronização rápida
        self.btn_sync = ctk.CTkButton(
            self.header_frame,
            text="🔄",
            width=26,
            height=26,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            hover_color=self.accent_hover,
            command=self._on_sync_click
        )
        self.btn_sync.pack(side="right", padx=(2, 6))

        # Corpo
        self.body_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.body_frame.pack(fill="both", expand=True, padx=6, pady=(0, 2))

        # 4 Mini Cards de Resumo
        summary_grid = ctk.CTkFrame(self.body_frame, fg_color="transparent")
        summary_grid.pack(fill="x", pady=(2, 6))
        summary_grid.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.pill_today = self._create_mini_pill(summary_grid, 0, "HOJE", "0", "#ef4444")
        self.pill_tomorrow = self._create_mini_pill(summary_grid, 1, "+2 DIAS", "0", "#f59e0b")
        self.pill_week = self._create_mini_pill(summary_grid, 2, "SEMANA", "0", self.accent_color)
        self.pill_overdue = self._create_mini_pill(summary_grid, 3, "ATRASO", "0", "#a855f7")

        # Lista Scrollável
        self.scroll_list = ctk.CTkScrollableFrame(
            self.body_frame,
            fg_color="#121215",
            corner_radius=8
        )
        self.scroll_list.pack(fill="both", expand=True)
        self.scroll_list.bind("<ButtonRelease-1>", lambda e: self.send_to_desktop_layer())

        # Rodapé com alça de redimensionamento (Resize Grip)
        footer_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent", height=24)
        footer_frame.pack(fill="x", padx=6, pady=(0, 4))
        footer_frame.pack_propagate(False)

        self.lbl_footer = ctk.CTkLabel(
            footer_frame,
            text="Aguardando sincronização...",
            font=ctk.CTkFont(size=10),
            text_color="#71717a"
        )
        self.lbl_footer.pack(side="left", padx=4)

        self.resize_grip = ctk.CTkLabel(
            footer_frame,
            text="⤡",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#71717a",
            cursor="size_nw_se"
        )
        self.resize_grip.pack(side="right", padx=(0, 4))
        self.resize_grip.bind("<ButtonPress-1>", self._start_resize)
        self.resize_grip.bind("<B1-Motion>", self._on_resize)
        self.resize_grip.bind("<ButtonRelease-1>", self._finish_resize)

    def _create_mini_pill(self, parent, col, title, count, color) -> Dict[str, Any]:
        frame = ctk.CTkFrame(parent, fg_color="#202025", corner_radius=6, border_width=0)
        frame.grid(row=0, column=col, padx=2, pady=2, sticky="nsew")

        lbl_t = ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=9, weight="bold"), text_color=color)
        lbl_t.pack(pady=(4, 0))

        lbl_c = ctk.CTkLabel(frame, text=count, font=ctk.CTkFont(size=14, weight="bold"), text_color="#ffffff")
        lbl_c.pack(pady=(0, 4))

        return {"count_lbl": lbl_c, "color": color}

    def update_data(self, classified: Optional[ClassifiedItems]):
        if not classified:
            return

        self.pill_today["count_lbl"].configure(text=str(len(classified.today)))
        self.pill_tomorrow["count_lbl"].configure(text=str(len(classified.tomorrow)))
        self.pill_week["count_lbl"].configure(text=str(len(classified.this_week)))
        self.pill_overdue["count_lbl"].configure(text=str(len(classified.overdue)))

        self.lbl_footer.configure(text=f"Atualizado às {datetime.now().strftime('%H:%M:%S')}")

        for child in self.scroll_list.winfo_children():
            child.destroy()

        has_any = False

        if classified.today:
            has_any = True
            self._render_section_header("🚨 VENCENDO HOJE", "#ef4444")
            for it in classified.today:
                self._render_item_row(it)

        if classified.tomorrow:
            has_any = True
            self._render_section_header("⚠️ AMANHÃ / PRÓXIMOS DIAS", "#f59e0b")
            for it in classified.tomorrow:
                self._render_item_row(it)

        if classified.this_week:
            has_any = True
            self._render_section_header("📅 ESTA SEMANA", self.accent_color)
            for it in classified.this_week:
                self._render_item_row(it)

        if classified.overdue:
            has_any = True
            self._render_section_header("⏳ ATRASADAS / PENDENTES", "#a855f7")
            for it in classified.overdue[:8]:
                self._render_item_row(it)

        if not has_any:
            ctk.CTkLabel(
                self.scroll_list,
                text="Nenhum prazo pendente.",
                font=ctk.CTkFont(size=11),
                text_color="#71717a"
            ).pack(pady=20)

    def _render_section_header(self, text: str, color: str):
        lbl = ctk.CTkLabel(
            self.scroll_list,
            text=text,
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=color,
            anchor="w"
        )
        lbl.pack(fill="x", padx=4, pady=(8, 2))

    def _render_item_row(self, it: Dict[str, Any]):
        card = ctk.CTkFrame(self.scroll_list, fg_color="#1c1c20", corner_radius=6, border_width=0)
        card.pack(fill="x", pady=2, padx=2)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=6, pady=(4, 1))

        ctk.CTkLabel(
            top,
            text=f"[{it['type']}]",
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color="#93c5fd" if it["type"] != "Tarefa" else "#fbbf24"
        ).pack(side="left")

        ctk.CTkLabel(
            top,
            text=it["deadline_str"],
            font=ctk.CTkFont(size=9),
            text_color="#a1a1aa"
        ).pack(side="right")

        ctk.CTkLabel(
            card,
            text=it["title"],
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#f4f4f5",
            anchor="w",
            justify="left",
            wraplength=max(200, self.current_w - 50)
        ).pack(fill="x", padx=6, pady=(0, 4))

    # --- Arraste de Janela & Persistência Contínua ---
    def _start_drag(self, event):
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _on_drag(self, event):
        deltax = event.x - self._drag_start_x
        deltay = event.y - self._drag_start_y
        new_x = self.winfo_x() + deltax
        new_y = self.winfo_y() + deltay
        self.geometry(f"+{new_x}+{new_y}")

    def _finish_drag(self, event):
        new_x = self.winfo_x()
        new_y = self.winfo_y()
        # Salva imediatamente e com precisão no SQLite
        self.db.set_setting("desktop_fence_x", str(new_x))
        self.db.set_setting("desktop_fence_y", str(new_y))
        self.send_to_desktop_layer()

    # --- Redimensionamento Visual via Grip (⤡) ---
    def _start_resize(self, event):
        self._resize_start_x = event.x_root
        self._resize_start_y = event.y_root
        self._orig_w = self.winfo_width()
        self._orig_h = self.winfo_height()

    def _on_resize(self, event):
        delta_w = event.x_root - self._resize_start_x
        delta_h = event.y_root - self._resize_start_y
        new_w = max(260, min(1000, self._orig_w + delta_w))
        new_h = max(300, min(1400, self._orig_h + delta_h))

        self.current_w = new_w
        self.current_h = new_h
        self.geometry(f"{new_w}x{new_h}+{self.winfo_x()}+{self.winfo_y()}")

    def _finish_resize(self, event):
        self.db.set_setting("desktop_fence_w", str(self.current_w))
        self.db.set_setting("desktop_fence_h", str(self.current_h))
        self.db.set_setting("desktop_fence_x", str(self.winfo_x()))
        self.db.set_setting("desktop_fence_y", str(self.winfo_y()))
        self.send_to_desktop_layer()

    def _on_sync_click(self):
        import threading
        threading.Thread(target=lambda: self.scheduler.sync_and_notify(force=True), daemon=True).start()

    def hide_widget(self):
        self._is_enabled = False
        self.db.set_setting("desktop_fence_enabled", "false")
        self.withdraw()

    def show_widget(self):
        self._is_enabled = True
        self.db.set_setting("desktop_fence_enabled", "true")
        self.deiconify()
        self.pin_to_desktop()
        self.after(50, self.pin_to_desktop)

    def set_main_window(self, win):
        self.main_window = win
