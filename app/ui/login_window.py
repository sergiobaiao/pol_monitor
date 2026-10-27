import threading
from typing import Optional, Tuple, Dict, Any

import customtkinter as ctk
import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from ..database import Database
from ..pol_client import keycloak_login, LoginError
from .theme import get_windows_accent_color, get_accent_hover

# Senha lembrada vai para o Windows Credential Manager (DPAPI), nunca para o SQLite
KEYRING_SERVICE = "POL ADV Notifier"


def load_remembered(app_db: Database) -> Optional[Tuple[str, str]]:
    email = app_db.get_setting("remembered_email", "")
    if not email:
        return None
    try:
        password = keyring.get_password(KEYRING_SERVICE, email)
    except KeyringError:
        password = None
    return (email, password) if password else None


def save_remembered(app_db: Database, email: str, password: str, remember: bool):
    try:
        if remember:
            keyring.set_password(KEYRING_SERVICE, email, password)
            app_db.set_setting("remembered_email", email)
            return
        keyring.delete_password(KEYRING_SERVICE, email)
    except PasswordDeleteError:
        pass  # nada salvo para esse e-mail
    except KeyringError as e:
        app_db.log("WARNING", f"Credential Manager indisponível: {e}")
    if app_db.get_setting("remembered_email", "").lower() == email.lower():
        app_db.delete_setting("remembered_email")


class LoginWindow(ctk.CTk):
    """Tela de login. Após `mainloop()`, `result` = (tokens, email, senha) ou None se fechada."""

    def __init__(self, app_db: Database, error: str = ""):
        super().__init__()
        self.app_db = app_db
        self.result: Optional[Tuple[Dict[str, Any], str, str]] = None

        accent = get_windows_accent_color()
        self.title("POL ADV — Entrar")
        self.geometry("400x440")
        self.resizable(False, False)
        self.configure(fg_color="#09090b")
        self.protocol("WM_DELETE_WINDOW", self.quit)

        card = ctk.CTkFrame(self, fg_color="#18181b", corner_radius=12)
        card.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(card, text="⚖️ POL ADV", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=accent).pack(pady=(28, 2))
        ctk.CTkLabel(card, text="Entre com sua conta do POL ADV", font=ctk.CTkFont(size=12),
                     text_color="#a1a1aa").pack(pady=(0, 20))

        self.ent_email = ctk.CTkEntry(card, placeholder_text="E-mail", height=36,
                                      fg_color="#27272a", border_color="#3f3f46")
        self.ent_email.pack(fill="x", padx=28, pady=6)
        self.ent_pass = ctk.CTkEntry(card, placeholder_text="Senha", show="*", height=36,
                                     fg_color="#27272a", border_color="#3f3f46")
        self.ent_pass.pack(fill="x", padx=28, pady=6)

        self.chk_remember = ctk.CTkCheckBox(card, text="Lembrar usuário/senha", font=ctk.CTkFont(size=12),
                                            fg_color=accent, hover_color=get_accent_hover(accent))
        self.chk_remember.pack(anchor="w", padx=28, pady=(10, 4))

        self.lbl_error = ctk.CTkLabel(card, text=error, text_color="#ef4444", font=ctk.CTkFont(size=12),
                                      wraplength=300, justify="center")
        self.lbl_error.pack(fill="x", padx=28, pady=(4, 4))

        self.btn_login = ctk.CTkButton(card, text="Entrar", height=38, fg_color=accent,
                                       hover_color=get_accent_hover(accent),
                                       font=ctk.CTkFont(size=13, weight="bold"), command=self._submit)
        self.btn_login.pack(fill="x", padx=28, pady=(6, 20))

        remembered = load_remembered(app_db)
        if remembered:
            self.ent_email.insert(0, remembered[0])
            self.ent_pass.insert(0, remembered[1])
            self.chk_remember.select()

        self.bind("<Return>", lambda e: self._submit())
        self.after(100, lambda: (self.lift(), self.focus_force(),
                                 (self.ent_pass if remembered else self.ent_email).focus_set()))

    def _submit(self):
        if self.btn_login.cget("state") == "disabled":
            return
        email = self.ent_email.get().strip()
        password = self.ent_pass.get()
        if not email or not password:
            self.lbl_error.configure(text="Informe e-mail e senha.")
            return
        self.lbl_error.configure(text="")
        self.btn_login.configure(state="disabled", text="Entrando...")
        threading.Thread(target=self._do_login, args=(email, password), daemon=True).start()

    def _do_login(self, email: str, password: str):
        try:
            tokens = keycloak_login(email, password)
            self.after(0, lambda: self._on_success(tokens, email, password))
        except LoginError as e:
            msg = str(e)
            self.after(0, lambda: self._on_error(msg))

    def _on_success(self, tokens: Dict[str, Any], email: str, password: str):
        save_remembered(self.app_db, email, password, bool(self.chk_remember.get()))
        self.result = (tokens, email, password)
        self.quit()

    def _on_error(self, msg: str):
        self.lbl_error.configure(text=msg)
        self.btn_login.configure(state="normal", text="Entrar")
        self.ent_pass.focus_set()


def run_login(app_db: Database, auto: bool = True) -> Optional[Tuple[Dict[str, Any], str, str]]:
    """Login antes de montar o app. Com credenciais lembradas e `auto`, entra sem mostrar a tela."""
    error = ""
    remembered = load_remembered(app_db) if auto else None
    if remembered:
        try:
            return keycloak_login(*remembered), remembered[0], remembered[1]
        except LoginError as e:
            error = str(e)
    win = LoginWindow(app_db, error)
    win.mainloop()
    result = win.result
    win.destroy()
    return result
