using System.ComponentModel;
using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using PolNotifier.Core;

namespace PolNotifier.Ui;

/// <summary>Painel principal (app/ui/main_window.py). Fechar/minimizar vão para a bandeja conforme a config.</summary>
public partial class MainWindow : Window
{
    static readonly (string Label, int Minutes)[] PresetValues =
        [("5 min", 5), ("15 min", 15), ("30 min", 30), ("1 hora", 60), ("2 horas", 120), ("4 horas", 240), ("8 horas", 480)];

    readonly Config _config;
    readonly Database _db;
    readonly PolClient _pol;
    readonly Scheduler _scheduler;
    readonly FenceWindow _fence;
    readonly TextBlock _mToday, _mTomorrow, _mWeek, _mOverdue;
    readonly List<(int Minutes, Button Button)> _presets = [];
    bool _loading = true;
    public bool AllowClose { get; set; }
    public event Action? QuitRequested, LogoutRequested;

    public MainWindow(Config config, PolClient pol, Scheduler scheduler, FenceWindow fence, string userName)
    {
        InitializeComponent();
        _config = config;
        _db = config.Db;
        _pol = pol;
        _scheduler = scheduler;
        _fence = fence;

        var h = DateTime.Now.Hour;
        LblGreeting.Text = $"{(h is >= 5 and < 12 ? "Bom dia" : h is >= 12 and < 18 ? "Boa tarde" : "Boa noite")}, {userName}";
        var br = CultureInfo.GetCultureInfo("pt-BR");
        var date = DateTime.Now.ToString("dddd, dd 'de' MMMM", br);
        LblSubtitle.Text = $"{char.ToUpper(date[0])}{date[1..]} • Monitoramento Contínuo";
        LblUser.Text = $"{config.PolEmail}\nEscritório: #{config.PolOfficeId}\nv2.0.0 (.NET WPF)";
        LblConnected.Text = $"Conectado como {config.PolEmail}";
        LblAccent.Text = $"Cor do Windows: {Theme.AccentHex}";

        _mToday = Metric("🚨 HOJE", "Vencendo hoje", "#ef4444");
        _mTomorrow = Metric("⚠️ PRÓXIMOS 2 DIAS", "Atenção necessária", "#f59e0b");
        _mWeek = Metric("📅 ESTA SEMANA", "Prazos em aberto", "#3b82f6");
        _mOverdue = Metric("⏳ ATRASADAS", "Tarefas pendentes", "#a855f7");

        foreach (var (label, mins) in PresetValues)
        {
            var b = new Button { Content = label, Style = (Style)FindResource("FlatButton"), Margin = new Thickness(0, 0, 6, 0), Padding = new Thickness(12, 5, 12, 5), FontSize = 11 };
            b.Click += (_, _) => SetIntervalUi(mins);
            Presets.Children.Add(b);
            _presets.Add((mins, b));
        }

        LoadSettingsIntoUi();
        _loading = false;
        UpdateFenceButton();

        StateChanged += (_, _) =>
        {
            if (WindowState == WindowState.Minimized && _config.MinimizeToTray) Hide();
        };
        IsVisibleChanged += (_, _) => { if (IsVisible) RefreshLogs(); };
    }

    void LoadSettingsIntoUi()
    {
        ChkEmail.IsChecked = _config.EmailEnabled;
        TxtSmtpHost.Text = _config.SmtpHost;
        TxtSmtpPort.Text = _config.SmtpPort.ToString();
        TxtSmtpUser.Text = _config.SmtpUser;
        TxtSmtpPass.Password = _config.SmtpPass;
        TxtSmtpRecipients.Text = string.Join(", ", _config.SmtpRecipients);
        ChkSmtpSsl.IsChecked = _config.SmtpUseSsl;
        ChkTelegram.IsChecked = _config.TelegramEnabled;
        TxtTgToken.Text = _config.TelegramToken;
        TxtTgChats.Text = string.Join(", ", _config.TelegramChatIds);
        ChkWa.IsChecked = _config.OpenWaEnabled;
        TxtWaUrl.Text = _config.OpenWaUrl;
        TxtWaKey.Text = _config.OpenWaApiKey;
        TxtWaRecipients.Text = string.Join(", ", _config.OpenWaRecipients);

        TxtOffice.Text = _config.PolOfficeId;
        SetIntervalUi(_config.CheckIntervalMinutes);
        TxtDailyHour.Text = _config.NotifyDailyHour;

        SldW.Value = _config.FenceW;
        SldH.Value = _config.FenceH;
        SldAlpha.Value = Math.Round(_config.FenceAlpha * 100);
        UpdateFenceLabels();

        ChkAutoStart.IsChecked = Config.StartWithWindows;
        ChkStartFence.IsChecked = _config.DesktopFenceEnabled;
        ChkStartMain.IsChecked = !_config.StartMinimized;
        ChkMinTray.IsChecked = _config.MinimizeToTray;
        ChkCloseTray.IsChecked = _config.CloseToTray;
    }

    TextBlock Metric(string title, string subtitle, string color)
    {
        var count = new TextBlock { Text = "0", FontSize = 28, FontWeight = FontWeights.Bold, Foreground = Brushes.White };
        var sp = new StackPanel { Margin = new Thickness(14, 12, 14, 12) };
        sp.Children.Add(new TextBlock { Text = title, FontSize = 11, FontWeight = FontWeights.Bold, Foreground = Theme.Brush(color) });
        sp.Children.Add(count);
        sp.Children.Add(new TextBlock { Text = subtitle, FontSize = 11, Foreground = Theme.Brush("#71717a") });
        Metrics.Children.Add(new Border
        {
            Background = Theme.Brush("#18181b"), BorderBrush = Theme.Brush("#27272a"), BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(12), Margin = new Thickness(6, 4, 6, 4), Child = sp,
        });
        return count;
    }

    // --- Navegação ---
    void OnNav(object sender, RoutedEventArgs e)
    {
        if (ViewHome == null) return; // disparado durante InitializeComponent
        var tag = (string)((FrameworkElement)sender).Tag;
        foreach (var (name, view) in new (string, UIElement)[] { ("Home", ViewHome), ("Tasks", ViewTasks), ("Calendar", ViewCalendar),
                                                                   ("Notifications", ViewNotifications), ("Settings", ViewSettings), ("Logs", ViewLogs) })
            view.Visibility = name == tag ? Visibility.Visible : Visibility.Collapsed;
        if (tag == "Logs") RefreshLogs();
    }

    // --- Dados ---
    public ClassifiedItems? LoadCached()
    {
        var items = _db.GetCachedItems();
        JsonObject Parse(string raw) { try { return JsonNode.Parse(raw) as JsonObject ?? []; } catch (System.Text.Json.JsonException) { return []; } }
        var tasks = items.Where(i => i.Type == "TASK").Select(i => Parse(i.RawJson)).ToList();
        var events = items.Where(i => i.Type == "EVENT").Select(i => Parse(i.RawJson)).ToList();
        if (tasks.Count + events.Count == 0) return null;
        UpdateData(Formatter.Classify(tasks, events));
        return _data;
    }

    ClassifiedItems? _data;

    public void UpdateData(ClassifiedItems c)
    {
        _data = c;
        _mToday.Text = c.Today.Count.ToString();
        _mTomorrow.Text = c.Tomorrow.Count.ToString();
        _mWeek.Text = c.ThisWeek.Count.ToString();
        _mOverdue.Text = c.Overdue.Count.ToString();

        Fill(FeedTasks, c.Upcoming.Select(it => Card(it, false)), "Nenhum prazo pendente para os próximos dias.");
        Fill(FeedEvents, c.Upcoming.Where(i => !i.IsTask).Select(it => Card(it, true)), "Nenhuma audiência marcada para esta semana.");
        Fill(CalendarList, c.Upcoming.Where(i => !i.IsTask).Select(CalendarRow), "Nenhum compromisso ou audiência agendada.");
        FilterTasks();
        if (ViewLogs.Visibility == Visibility.Visible) RefreshLogs();
    }

    void Fill(StackPanel panel, IEnumerable<UIElement> rows, string empty)
    {
        panel.Children.Clear();
        foreach (var r in rows) panel.Children.Add(r);
        if (panel.Children.Count == 0)
            panel.Children.Add(new TextBlock { Text = empty, Foreground = Theme.Brush("#71717a"), HorizontalAlignment = HorizontalAlignment.Center, Margin = new Thickness(20) });
    }

    static Border Box(UIElement child, double margin = 4) => new()
    {
        Background = Theme.Brush("#27272a"), CornerRadius = new CornerRadius(8), Margin = new Thickness(margin), Child = child, Padding = new Thickness(10, 8, 10, 8),
    };

    static DockPanel Head(UIElement left, UIElement right)
    {
        var d = new DockPanel { Margin = new Thickness(0, 0, 0, 4) };
        DockPanel.SetDock(right, Dock.Right);
        d.Children.Add(right);
        d.Children.Add(left);
        return d;
    }

    static TextBlock T(string text, double size, string color, bool bold = false, bool wrap = false) => new()
    {
        Text = text, FontSize = size, Foreground = Theme.Brush(color), FontWeight = bold ? FontWeights.Bold : FontWeights.Normal,
        TextWrapping = wrap ? TextWrapping.Wrap : TextWrapping.NoWrap, TextTrimming = wrap ? TextTrimming.None : TextTrimming.CharacterEllipsis,
    };

    UIElement Card(Item it, bool isEvent)
    {
        var badgeColor = isEvent ? "#3b82f6" : _data?.Today.Contains(it) == true ? "#ef4444" : "#eab308";
        var badge = new Border { Background = Theme.Brush(badgeColor), CornerRadius = new CornerRadius(4), Padding = new Thickness(5, 1, 5, 1), HorizontalAlignment = HorizontalAlignment.Left, Child = T(it.Type, 10, "#ffffff", true) };
        var sp = new StackPanel();
        sp.Children.Add(Head(badge, T(it.DeadlineStr, 11, "#a1a1aa")));
        sp.Children.Add(T(it.Title, 12, "#f4f4f5", true, wrap: true));
        return Box(sp);
    }

    UIElement CalendarRow(Item ev)
    {
        var sp = new StackPanel();
        sp.Children.Add(Head(T($"⚖️ {ev.Title}", 14, "#f4f4f5", true), T($"🕒 {ev.DeadlineStr}", 12, "#34d399", true)));
        if (ev.Description != "") sp.Children.Add(T($"Local/Pauta: {Short(ev.Description)}", 12, "#d1d5db", wrap: true));
        return Box(sp, 6);
    }

    static string Short(string s) => s.Length > 500 ? s[..500] + "…" : s;

    void OnFilterChanged(object sender, RoutedEventArgs e) => FilterTasks();

    void FilterTasks()
    {
        if (TasksList == null || _data is not { } c) return;
        IEnumerable<Item> items = CmbFilter.SelectedIndex switch
        {
            1 => c.Today, 2 => c.Tomorrow, 3 => c.ThisWeek, 4 => c.Overdue, _ => c.All,
        };
        var q = TxtSearch.Text.Trim();
        items = items.Where(i => i.IsTask);
        if (q != "") items = items.Where(i => i.Title.Contains(q, StringComparison.OrdinalIgnoreCase) || i.Description.Contains(q, StringComparison.OrdinalIgnoreCase));
        Fill(TasksList, items.Select(it =>
        {
            var sp = new StackPanel();
            sp.Children.Add(Head(T(it.Title, 13, "#f4f4f5", true), T($"📅 {it.DeadlineStr}", 12, "#60a5fa", true)));
            if (it.Description != "") sp.Children.Add(T(Short(it.Description), 11, "#9ca3af", wrap: true));
            return (UIElement)Box(sp, 4);
        }), "Nenhuma tarefa corresponde aos filtros selecionados.");
    }

    void RefreshLogs()
    {
        var sb = new StringBuilder();
        foreach (var l in _db.GetLogs(100)) sb.AppendLine($"[{l["timestamp"]}] [{l["level"]}] {l["message"]}");
        sb.AppendLine().AppendLine("── Últimas notificações enviadas ──");
        foreach (var n in _db.GetRecentNotifications(30).GroupBy(n => (n["sent_date"], n["channel"], n["status"])))
            sb.AppendLine($"[{n.First()["sent_at"]}] {n.Key.Item2} {n.Key.Item3} ({n.Count()} itens) → {n.First()["recipient"]} {n.First()["error_message"]}");
        TxtLogs.Text = sb.ToString();
    }

    void OnReloadLogs(object sender, RoutedEventArgs e) => RefreshLogs();
    void OnClearLogs(object sender, RoutedEventArgs e) { _db.ClearLogs(); RefreshLogs(); }

    // --- Sincronização ---
    public void SetStatus(string text, string color = "#10b981") { LblStatus.Text = text; LblStatus.Foreground = Theme.Brush(color); }

    async void OnSyncNow(object sender, RoutedEventArgs e)
    {
        BtnSync.IsEnabled = false;
        BtnSync.Content = "⏳ Sincronizando...";
        SetStatus("🟡 Sincronizando...", "#f59e0b");
        await Task.Run(() => _scheduler.SyncAndNotify(force: true));
        BtnSync.IsEnabled = true;
        BtnSync.Content = "🔄 Sincronizar Agora";
        SetStatus($"🟢 Atualizado {DateTime.Now:HH:mm}");
    }

    void OnManualSync(object sender, RoutedEventArgs e)
    {
        Alert.Show(this, "Sincronização", "Sincronização iniciada em segundo plano.");
        _ = Task.Run(() => _scheduler.SyncAndNotify(force: true));
    }

    // --- Widget ---
    void OnToggleFence(object sender, RoutedEventArgs e)
    {
        if (_fence.IsVisible) _fence.HideWidget(); else _fence.ShowWidget();
        UpdateFenceButton();
    }

    public void UpdateFenceButton() => BtnFence.Background = Theme.Brush(_fence.IsVisible ? "#1e3a8a" : "#27272a");

    void UpdateFenceLabels()
    {
        LblW.Text = $"Largura: {(int)SldW.Value} px";
        LblH.Text = $"Altura: {(int)SldH.Value} px";
        LblAlpha.Text = $"Opacidade / Transparência: {(int)SldAlpha.Value}%";
    }

    void OnFenceSize(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if (_loading) return;
        UpdateFenceLabels();
        _fence.SetDimensions((int)SldW.Value, (int)SldH.Value);
    }

    void OnFenceAlpha(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if (_loading) return;
        UpdateFenceLabels();
        _fence.SetOpacity(SldAlpha.Value / 100.0);
    }

    void OnResetFence(object sender, RoutedEventArgs e)
    {
        SldW.Value = 360;
        SldH.Value = 600;
        SldAlpha.Value = 90;
        OnSaveFence(sender, e);
    }

    void OnSaveFence(object sender, RoutedEventArgs e)
    {
        _fence.SetDimensions((int)SldW.Value, (int)SldH.Value);
        _fence.SetOpacity(SldAlpha.Value / 100.0);
        Alert.Show(this, "Sucesso", "Dimensões e transparência salvas com sucesso!");
    }

    // --- Intervalo ---
    void SetIntervalUi(int mins)
    {
        var wasLoading = _loading;
        _loading = true;
        SldInterval.Value = Math.Min(480, mins);
        if (TxtInterval.Text != mins.ToString()) TxtInterval.Text = mins.ToString();
        _loading = wasLoading;
        var text = $"Ajuste Fino: {mins} minuto{(mins != 1 ? "s" : "")}";
        if (mins >= 60) text += $" ({mins / 60.0:0.0}h)";
        LblInterval.Text = text;
        LblIntervalBadge.Text = $"Intervalo: {mins} min";
        foreach (var (m, b) in _presets) b.Background = m == mins ? (Brush)FindResource("AccentBrush") : Theme.Brush("#27272a");
    }

    void OnIntervalSlider(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if (!_loading) SetIntervalUi((int)e.NewValue);
    }

    void OnIntervalText(object sender, TextChangedEventArgs e)
    {
        if (!_loading && int.TryParse(TxtInterval.Text, out var v) && v is >= 1 and <= 1440) SetIntervalUi(v);
    }

    void OnSaveInterval(object sender, RoutedEventArgs e)
    {
        var mins = int.TryParse(TxtInterval.Text, out var v) ? Math.Clamp(v, 1, 1440) : 60;
        var daily = TxtDailyHour.Text.Trim() is { Length: > 0 } d ? d : "08:00";
        _config.CheckIntervalMinutes = mins;
        _config.NotifyDailyHour = daily;
        SetIntervalUi(mins);
        _scheduler.RestartCycle(); // acorda o agendador para o novo ciclo
        _db.Log("INFO", $"Período de atualização reconfigurado para {mins} minutos (disparo diário às {daily}).");
        Alert.Show(this, "Sucesso", $"Período de atualização salvo com sucesso!\nO agendador agora verifica a cada {mins} minutos.");
    }

    // --- Conta POL ---
    async void OnTestPol(object sender, RoutedEventArgs e)
    {
        _db.Log("INFO", $"Testando autenticação para {_config.PolEmail}...");
        if (await _pol.Authenticate())
        {
            _db.Log("INFO", "Autenticação POL ADV validada com sucesso!");
            Alert.Show(this, "Sucesso", "Autenticação na API do POL ADV realizada com sucesso!");
        }
        else Alert.Show(this, "Erro", "Falha na autenticação. Use \"Trocar Usuário\" para entrar novamente.", isError: true);
    }

    void OnSavePol(object sender, RoutedEventArgs e)
    {
        _config.PolOfficeId = TxtOffice.Text;
        _db.Log("INFO", "Configurações POL salvas com sucesso.");
        Alert.Show(this, "Sucesso", "Configurações POL salvas com sucesso!");
    }

    void OnLogout(object sender, RoutedEventArgs e) => LogoutRequested?.Invoke();

    // --- Inicialização ---
    void OnSaveStartup(object sender, RoutedEventArgs e)
    {
        Config.StartWithWindows = ChkAutoStart.IsChecked == true;
        _config.DesktopFenceEnabled = ChkStartFence.IsChecked == true;
        _config.StartMinimized = ChkStartMain.IsChecked != true;
        _config.MinimizeToTray = ChkMinTray.IsChecked == true;
        _config.CloseToTray = ChkCloseTray.IsChecked == true;
        _db.Log("INFO", $"Opções de janela e inicialização salvas: Windows={Config.StartWithWindows}, Fence={_config.DesktopFenceEnabled}, Painel={!_config.StartMinimized}, MinTray={_config.MinimizeToTray}, CloseTray={_config.CloseToTray}");
        Alert.Show(this, "Sucesso", "Opções de janela e inicialização salvas com sucesso!");
    }

    // --- Notificações ---
    void OnSaveNotifications(object sender, RoutedEventArgs e)
    {
        SaveNotificationFields();
        _db.Log("INFO", "Canais de notificação atualizados.");
        Alert.Show(this, "Sucesso", "Configurações de notificação salvas com sucesso!");
    }

    void SaveNotificationFields()
    {
        _config.EmailEnabled = ChkEmail.IsChecked == true;
        _config.SmtpHost = TxtSmtpHost.Text;
        _config.SmtpPort = int.TryParse(TxtSmtpPort.Text, out var p) ? p : 587;
        _config.SmtpUser = TxtSmtpUser.Text;
        _config.SmtpPass = TxtSmtpPass.Password;
        _config.SmtpRecipientsRaw = TxtSmtpRecipients.Text;
        _config.SmtpUseSsl = ChkSmtpSsl.IsChecked == true;
        _config.TelegramEnabled = ChkTelegram.IsChecked == true;
        _config.TelegramToken = TxtTgToken.Text;
        _config.TelegramChatIdsRaw = TxtTgChats.Text;
        _config.OpenWaEnabled = ChkWa.IsChecked == true;
        _config.OpenWaUrl = TxtWaUrl.Text;
        _config.OpenWaApiKey = TxtWaKey.Text;
        _config.OpenWaRecipientsRaw = TxtWaRecipients.Text;
    }

    // Testar usa exatamente o que está digitado: salva os campos antes (no Python, servidor/senha
    // vinham do que estava salvo e o teste falhava com "Authentication Required" sem salvar antes)
    async Task RunTest(Button b, Func<Task<(bool, string)>> send)
    {
        SaveNotificationFields();
        b.IsEnabled = false;
        try
        {
            var (ok, msg) = await send();
            // Envio parcial (alguns destinatários falharam) é aviso, não erro
            var partial = ok && msg.Contains("falha", StringComparison.OrdinalIgnoreCase);
            Alert.Show(this, ok ? (partial ? "Enviado com falhas" : "Teste enviado com sucesso") : "Falha no teste", msg,
                       isError: !ok, isWarning: partial);
        }
        finally { b.IsEnabled = true; }
    }

    async void OnTestEmail(object sender, RoutedEventArgs e)
    {
        var to = Config.SplitList(TxtSmtpRecipients.Text);
        if (to.Count == 0) { Alert.Show(this, "Atenção", "Preencha ao menos um destinatário de e-mail."); return; }
        // O teste manda o relatório real atual, para conferir o layout que vai chegar todo dia
        var data = _data ?? new ClassifiedItems();
        await RunTest((Button)sender, () => _scheduler.Notifiers.SendEmail("[Teste] " + Formatter.EmailSubject(data),
            Formatter.ToHtml(data, _scheduler.FirstName), to));
    }

    async void OnTestTelegram(object sender, RoutedEventArgs e)
    {
        var chats = Config.SplitList(TxtTgChats.Text);
        if (chats.Count == 0) { Alert.Show(this, "Atenção", "Preencha ao menos um Chat ID do Telegram."); return; }
        await RunTest((Button)sender, () => _scheduler.Notifiers.SendTelegram(
            "🧪 *Teste do POL ADV Desktop Notifier*\nMensagem de teste enviada com sucesso!", chats));
    }

    async void OnTestWa(object sender, RoutedEventArgs e)
    {
        var to = Config.SplitList(TxtWaRecipients.Text);
        if (to.Count == 0) { Alert.Show(this, "Atenção", "Preencha ao menos um número de WhatsApp."); return; }
        await RunTest((Button)sender, () => _scheduler.Notifiers.SendOpenWa(
            "*⚖️ POL ADV Desktop Notifier*\nTeste de envio via WhatsApp concluído!", to));
    }

    // --- Bandeja ---
    protected override void OnClosing(CancelEventArgs e)
    {
        if (AllowClose) return;
        e.Cancel = true;
        if (_config.CloseToTray) Hide();
        else QuitRequested?.Invoke();
    }

    public void ShowFromTray()
    {
        Show();
        if (WindowState == WindowState.Minimized) WindowState = WindowState.Normal;
        Activate();
    }
}
