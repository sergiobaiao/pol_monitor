import threading
from typing import Callable, Optional
import pystray
from PIL import Image, ImageDraw

def create_tray_icon_image() -> Image.Image:
    width = 64
    height = 64
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Círculo de fundo azul escuro
    draw.ellipse((4, 4, 60, 60), fill="#1e3a8a", outline="#3b82f6", width=2)
    
    # Haste central da balança
    draw.line((32, 16, 32, 48), fill="#ffffff", width=3)
    # Base
    draw.line((22, 48, 42, 48), fill="#ffffff", width=3)
    # Braço horizontal
    draw.line((18, 24, 46, 24), fill="#ffffff", width=3)
    # Pratos esquerdo e direito
    draw.line((18, 24, 14, 34), fill="#93c5fd", width=2)
    draw.line((18, 24, 22, 34), fill="#93c5fd", width=2)
    draw.arc((12, 32, 24, 40), 0, 180, fill="#ffffff", width=2)

    draw.line((46, 24, 42, 34), fill="#93c5fd", width=2)
    draw.line((46, 24, 50, 34), fill="#93c5fd", width=2)
    draw.arc((40, 32, 52, 40), 0, 180, fill="#ffffff", width=2)

    return image

class TrayIconManager:
    def __init__(self, on_open_click: Callable[[], None],
                 on_sync_click: Callable[[], None],
                 on_toggle_fence_click: Optional[Callable[[], None]] = None,
                 on_quit_click: Callable[[], None] = None,
                 on_logout_click: Optional[Callable[[], None]] = None):
        self.on_open = on_open_click
        self.on_sync = on_sync_click
        self.on_toggle_fence = on_toggle_fence_click
        self.on_quit = on_quit_click
        self.on_logout = on_logout_click
        self.icon: Optional[pystray.Icon] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        image = create_tray_icon_image()
        menu_items = [
            pystray.MenuItem("Abrir Painel POL ADV", self._on_open, default=True),
            pystray.MenuItem("Sincronizar Prazos Agora", self._on_sync),
            pystray.MenuItem("🖥️ Exibir/Ocultar Widget Desktop (Fences)", self._on_toggle_fence),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("👤 Trocar Usuário", self._on_logout),
            pystray.MenuItem("Encerrar Aplicação", self._on_quit)
        ]
        self.icon = pystray.Icon("pol_adv_notifier", image, "POL ADV — Notificador de Prazos", pystray.Menu(*menu_items))
        self._thread = threading.Thread(target=self.icon.run, daemon=True, name="TrayIconThread")
        self._thread.start()

    def _on_open(self, icon=None, item=None):
        if self.on_open:
            self.on_open()

    def _on_sync(self, icon=None, item=None):
        if self.on_sync:
            self.on_sync()

    def _on_toggle_fence(self, icon=None, item=None):
        if self.on_toggle_fence:
            self.on_toggle_fence()

    def _on_quit(self, icon=None, item=None):
        if self.icon:
            self.icon.stop()
        if self.on_quit:
            self.on_quit()

    def _on_logout(self, icon=None, item=None):
        if self.icon:
            self.icon.stop()
        if self.on_logout:
            self.on_logout()

    def stop(self):
        if self.icon:
            self.icon.stop()
