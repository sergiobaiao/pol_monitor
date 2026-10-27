import winreg
from typing import Tuple

def get_windows_accent_color() -> str:
    """Obtém a cor de destaque (Accent Color) configurada no Windows pelo usuário."""
    # 1. Tentar AccentColor (DWM)
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM")
        val, _ = winreg.QueryValueEx(key, "AccentColor")
        winreg.CloseKey(key)
        # AccentColor é 0xAABBGGRR
        r = val & 0xFF
        g = (val >> 8) & 0xFF
        b = (val >> 16) & 0xFF
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        pass

    # 2. Tentar ColorizationColor (DWM)
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM")
        val, _ = winreg.QueryValueEx(key, "ColorizationColor")
        winreg.CloseKey(key)
        # ColorizationColor é 0xAARRGGBB
        r = (val >> 16) & 0xFF
        g = (val >> 8) & 0xFF
        b = val & 0xFF
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        pass

    return "#0078d4" # Padrão Windows Blue

def get_accent_hover(hex_color: str, factor: float = 0.85) -> str:
    """Gera uma versão ligeiramente mais escura/clara para estados de hover."""
    try:
        hex_clean = hex_color.lstrip("#")
        r, g, b = tuple(int(hex_clean[i:i+2], 16) for i in (0, 2, 4))
        r = int(min(255, max(0, r * factor)))
        g = int(min(255, max(0, g * factor)))
        b = int(min(255, max(0, b * factor)))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return "#1d4ed8"

def is_windows_dark_theme() -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return val == 0
    except Exception:
        return True
