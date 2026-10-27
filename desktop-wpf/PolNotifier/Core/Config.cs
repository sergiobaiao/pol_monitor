namespace PolNotifier.Core;

/// <summary>Configurações por usuário (tabela settings). Mesmas chaves e padrões de app/config.py.</summary>
public sealed class Config(Database db)
{
    public Database Db { get; } = db;

    string Str(string key, string def) => Db.GetSetting(key, def) ?? def;
    bool Bool(string key, bool def) => Str(key, def ? "true" : "false").Equals("true", StringComparison.OrdinalIgnoreCase);
    void SetBool(string key, bool val) => Db.SetSetting(key, val ? "true" : "false");
    int Int(string key, int def) => int.TryParse(Db.GetSetting(key), out var v) ? v : def;
    double Dbl(string key, double def) =>
        double.TryParse(Db.GetSetting(key), System.Globalization.NumberStyles.Float,
                        System.Globalization.CultureInfo.InvariantCulture, out var v) ? v : def;
    static List<string> Split(string raw) =>
        raw.Replace(';', ',').Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).ToList();

    // --- Conta POL ADV ---
    public string PolEmail { get => Str("pol_email", ""); set => Db.SetSetting("pol_email", value.Trim()); }
    public string PolOfficeId { get => Str("pol_office_id", ""); set => Db.SetSetting("pol_office_id", value.Trim()); }
    public string PolUserId { get => Str("pol_user_id", ""); set => Db.SetSetting("pol_user_id", value.Trim()); }

    // --- Janelas ---
    public bool StartMinimized { get => Bool("start_minimized", true); set => SetBool("start_minimized", value); }
    public bool DesktopFenceEnabled { get => Bool("desktop_fence_enabled", true); set => SetBool("desktop_fence_enabled", value); }
    public bool CloseToTray { get => Bool("close_to_tray", true); set => SetBool("close_to_tray", value); }
    public bool MinimizeToTray { get => Bool("minimize_to_tray", true); set => SetBool("minimize_to_tray", value); }

    /// <summary>Início automático: valor no Run do usuário (o mesmo que o instalador NSIS grava),
    /// não no SQLite, porque vale para o Windows e não para o usuário do POL.</summary>
    public static bool StartWithWindows
    {
        get
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(RunKey);
            return key?.GetValue(RunValue) is string;
        }
        set
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.CreateSubKey(RunKey);
            if (value) key.SetValue(RunValue, $"\"{Environment.ProcessPath}\"");
            else key.DeleteValue(RunValue, throwOnMissingValue: false);
        }
    }
    const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run", RunValue = "POL ADV Notifier";

    // --- Widget (x/y em pixels físicos, w/h em pixels lógicos, como o Tk gravava) ---
    public int? FenceX { get => int.TryParse(Db.GetSetting("desktop_fence_x"), out var v) ? v : null; set => Db.SetSetting("desktop_fence_x", value?.ToString()); }
    public int? FenceY { get => int.TryParse(Db.GetSetting("desktop_fence_y"), out var v) ? v : null; set => Db.SetSetting("desktop_fence_y", value?.ToString()); }
    public int FenceW { get => Int("desktop_fence_w", 360); set => Db.SetSetting("desktop_fence_w", value.ToString()); }
    public int FenceH { get => Int("desktop_fence_h", 600); set => Db.SetSetting("desktop_fence_h", value.ToString()); }
    public double FenceAlpha
    {
        get => Math.Clamp(Dbl("desktop_fence_alpha", 0.90), 0.2, 1.0);
        set => Db.SetSetting("desktop_fence_alpha", Math.Clamp(value, 0.2, 1.0).ToString("0.00", System.Globalization.CultureInfo.InvariantCulture));
    }
    public bool FenceCollapsed { get => Bool("desktop_fence_collapsed", false); set => SetBool("desktop_fence_collapsed", value); }

    // --- Intervalo ---
    public int CheckIntervalMinutes { get => Int("check_interval_minutes", 60); set => Db.SetSetting("check_interval_minutes", Math.Max(1, value).ToString()); }
    public string NotifyDailyHour { get => Str("notify_daily_hour", "08:00"); set => Db.SetSetting("notify_daily_hour", value.Trim()); }

    // --- E-mail (SMTP) ---
    public bool EmailEnabled { get => Bool("email_enabled", false); set => SetBool("email_enabled", value); }
    public string SmtpHost { get => Str("smtp_host", "smtp.gmail.com"); set => Db.SetSetting("smtp_host", value.Trim()); }
    public int SmtpPort { get => Int("smtp_port", 587); set => Db.SetSetting("smtp_port", value.ToString()); }
    public string SmtpUser { get => Str("smtp_user", ""); set => Db.SetSetting("smtp_user", value.Trim()); }
    public string SmtpPass { get => Str("smtp_pass", ""); set => Db.SetSetting("smtp_pass", value); }
    public string SmtpFrom { get => Str("smtp_from", ""); set => Db.SetSetting("smtp_from", value.Trim()); }
    public List<string> SmtpRecipients => Split(Str("smtp_recipients", ""));
    public string SmtpRecipientsRaw { set => Db.SetSetting("smtp_recipients", value.Trim()); }
    public bool SmtpUseSsl { get => Bool("smtp_use_ssl", false); set => SetBool("smtp_use_ssl", value); }

    // --- Telegram ---
    public bool TelegramEnabled { get => Bool("telegram_enabled", false); set => SetBool("telegram_enabled", value); }
    public string TelegramToken { get => Str("telegram_token", ""); set => Db.SetSetting("telegram_token", value.Trim()); }
    public List<string> TelegramChatIds => Split(Str("telegram_chat_ids", ""));
    public string TelegramChatIdsRaw { set => Db.SetSetting("telegram_chat_ids", value.Trim()); }

    // --- WhatsApp (OpenWA) ---
    public bool OpenWaEnabled { get => Bool("openwa_enabled", false); set => SetBool("openwa_enabled", value); }
    public string OpenWaUrl { get => Str("openwa_url", "http://localhost:8080"); set => Db.SetSetting("openwa_url", value.Trim().TrimEnd('/')); }
    public string OpenWaApiKey { get => Str("openwa_api_key", ""); set => Db.SetSetting("openwa_api_key", value.Trim()); }
    public List<string> OpenWaRecipients => Split(Str("openwa_recipients", ""));
    public string OpenWaRecipientsRaw { set => Db.SetSetting("openwa_recipients", value.Trim()); }

    public static List<string> SplitList(string raw) => Split(raw);
}
