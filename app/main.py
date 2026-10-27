import os
import sys

# Garantir que o diretório raiz esteja no path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.database import Database, open_user_db
from app.config import Config
from app.pol_client import PolClient, jwt_claims
from app.scheduler import Scheduler
from app.ui.login_window import run_login
from app.ui.main_window import MainWindow
from app.ui.desktop_fence import DesktopFenceWidget
from app.ui.tray_icon import TrayIconManager


def main():
    # Banco global: só guarda qual e-mail foi lembrado; o resto é por usuário
    app_db = Database()
    session = run_login(app_db)
    # Trocar usuário desmonta tudo e volta para a tela de login (sem auto-login)
    while session and run_app(*session):
        session = run_login(app_db, auto=False)


def run_app(tokens, email: str, password: str) -> bool:
    """Monta o app para o usuário logado. Retorna True se ele pediu para trocar de usuário."""
    db = open_user_db(jwt_claims(tokens["access_token"])["sub"], email)
    config = Config(db)
    pol_client = PolClient(config, db)
    pol_client.start_session(tokens, email, password)

    scheduler = Scheduler(config, db, pol_client)

    # 1. Inicializa o Desktop Widget como janela raiz (CTk) fixada no Desktop
    fence = DesktopFenceWidget(config, db, scheduler)

    # 2. Inicializa o Painel de Controle como CTkToplevel vinculado à raiz
    app = MainWindow(fence, config, db, pol_client, scheduler)
    logout_requested = False
    app.set_desktop_fence(fence)
    fence.set_main_window(app)

    # Conectar atualização do Fence aos ciclos do Scheduler
    original_on_sync = scheduler.on_sync_complete
    def on_sync_done(classified):
        if original_on_sync:
            original_on_sync(classified)
        fence.after(0, lambda: fence.update_data(classified))
    scheduler.on_sync_complete = on_sync_done

    # Exibir ou ocultar o widget conforme preferência (padrão: ativo no desktop)
    if config.desktop_fence_enabled:
        fence.show_widget()
    else:
        fence.hide_widget()

    # Exibir ou ocultar a janela principal conforme configuração (padrão: minimizada na bandeja)
    if config.start_minimized:
        app.withdraw()
    else:
        app.deiconify()

    def on_open():
        fence.after(0, app.show_from_tray)

    def on_sync():
        scheduler.sync_and_notify(force=True)

    def on_toggle_fence():
        if fence.winfo_viewable():
            fence.after(0, fence.hide_widget)
        else:
            fence.after(0, fence.show_widget)

    def on_quit():
        scheduler.stop()
        # Uma sincronização em andamento não pode tocar a UI que será destruída
        scheduler.on_sync_complete = None
        scheduler.on_status_change = None
        tray.stop()
        try:
            app.destroy()
        except Exception:
            pass
        fence.after(0, fence.destroy)

    def on_logout():
        nonlocal logout_requested
        logout_requested = True
        on_quit()

    # Conectar encerramento limpo caso close_to_tray esteja desativado
    app.on_quit_callback = on_quit
    app.on_logout_callback = on_logout

    # Inicia ícone na bandeja do sistema
    tray = TrayIconManager(
        on_open_click=on_open,
        on_sync_click=on_sync,
        on_toggle_fence_click=on_toggle_fence,
        on_quit_click=on_quit,
        on_logout_click=on_logout
    )
    tray.start()

    # Inicia agendador em background
    scheduler.start()

    # Loop da interface
    try:
        fence.mainloop()
    finally:
        scheduler.stop()
        tray.stop()
    return logout_requested

if __name__ == "__main__":
    main()
