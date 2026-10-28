using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;
using PolNotifier.Core;

namespace PolNotifier.Ui;

/// <summary>Widget de desktop estilo Fences (app/ui/desktop_fence.py).
///
/// Fica preso ao desktop como janela top-level WS_EX_TOOLWINDOW cujo OWNER é o Progman, mantida em
/// HWND_BOTTOM: assim sobrevive ao Win + D e fica atrás dos apps. Não usar SetParent no Progman/WorkerW
/// (no Windows 11 24H2+ o Progman tem WS_EX_NOREDIRECTIONBITMAP e filhos de outro processo não são
/// compostos) e nunca restaurar o widget por conta própria ao ser minimizado/oculto (o segundo Win + D
/// deixaria de devolver as janelas abertas).</summary>
public partial class FenceWindow : Window
{
    const int GWL_EXSTYLE = -20, GWLP_HWNDPARENT = -8, GW_OWNER = 4;
    const long WS_EX_TOOLWINDOW = 0x80, WS_EX_APPWINDOW = 0x40000, WS_EX_NOACTIVATE = 0x08000000;
    const uint SWP_NOSIZE = 0x1, SWP_NOMOVE = 0x2, SWP_NOZORDER = 0x4, SWP_NOACTIVATE = 0x10;
    static readonly IntPtr HWND_BOTTOM = 1;

    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr FindWindowW(string cls, string? title);
    [DllImport("user32.dll")] static extern IntPtr GetWindowLongPtrW(IntPtr h, int i);
    [DllImport("user32.dll")] static extern IntPtr SetWindowLongPtrW(IntPtr h, int i, IntPtr v);
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint f);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
    [StructLayout(LayoutKind.Sequential)] struct RECT { public int Left, Top, Right, Bottom; }

    const int MinW = 260, MaxW = 1000, MinH = 300, MaxH = 1400, CollapsedH = 50;

    readonly Config _config;
    readonly Scheduler _scheduler;
    readonly DispatcherTimer _pinTimer = new() { Interval = TimeSpan.FromSeconds(1) };
    readonly TextBlock _cToday, _cTomorrow, _cWeek, _cOverdue;
    IntPtr _hwnd;
    bool _collapsed;

    public FenceWindow(Config config, Scheduler scheduler)
    {
        InitializeComponent();
        _config = config;
        _scheduler = scheduler;
        Opacity = config.FenceAlpha;
        Width = Math.Clamp(config.FenceW, MinW, MaxW);
        Height = Math.Clamp(config.FenceH, MinH, MaxH);
        if (config.FenceX is null || config.FenceY is null)
        {
            Left = Math.Max(20, SystemParameters.WorkArea.Right - Width - 40);
            Top = 100;
        }

        _cToday = Pill("HOJE", Formatter.UrgencyColor(0, dark: true));
        _cTomorrow = Pill("+2 DIAS", Formatter.UrgencyColor(1.5, dark: true));
        _cWeek = Pill("SEMANA", Formatter.UrgencyColor(5, dark: true));
        _cOverdue = Pill("ATRASO", Formatter.UrgencyColor(-1, dark: true));

        SetCollapsed(config.FenceCollapsed, save: false);
        SourceInitialized += (_, _) => OnSourceInitialized();
        Deactivated += (_, _) => SendToBottom();
        _pinTimer.Tick += (_, _) => MaintainPin();
        _pinTimer.Start();
        Closed += (_, _) => _pinTimer.Stop();
    }

    void OnSourceInitialized()
    {
        _hwnd = new WindowInteropHelper(this).Handle;
        // Fora do Alt+Tab e da barra de tarefas; clicar nele não o traz para a frente dos apps
        var ex = (long)GetWindowLongPtrW(_hwnd, GWL_EXSTYLE);
        SetWindowLongPtrW(_hwnd, GWL_EXSTYLE, (IntPtr)((ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW));
        // Posição salva em pixels físicos (como o Tk gravava); sem clamp de tela, por causa do multi-monitor
        if (_config.FenceX is int x && _config.FenceY is int y)
            SetWindowPos(_hwnd, IntPtr.Zero, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
        Pin();
    }

    void Pin()
    {
        var progman = FindWindowW("Progman", null);
        if (_hwnd == IntPtr.Zero || progman == IntPtr.Zero) return;
        if (GetWindow(_hwnd, GW_OWNER) != progman) SetWindowLongPtrW(_hwnd, GWLP_HWNDPARENT, progman);
        SendToBottom();
    }

    void SendToBottom()
    {
        if (_hwnd != IntPtr.Zero)
            SetWindowPos(_hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
    }

    /// <summary>Refaz o owner se o Explorer reiniciou e reaplica HWND_BOTTOM.</summary>
    void MaintainPin()
    {
        if (IsVisible) Pin();
    }

    TextBlock Pill(string title, string color)
    {
        var count = new TextBlock { Text = "0", FontSize = 14, FontWeight = FontWeights.Bold, Foreground = Brushes.White, HorizontalAlignment = HorizontalAlignment.Center };
        var sp = new StackPanel { Margin = new Thickness(0, 4, 0, 4) };
        sp.Children.Add(new TextBlock { Text = title, FontSize = 9, FontWeight = FontWeights.Bold, Foreground = Theme.Brush(color), HorizontalAlignment = HorizontalAlignment.Center });
        sp.Children.Add(count);
        Pills.Children.Add(new Border { Background = Theme.Brush("#202025"), CornerRadius = new CornerRadius(6), Margin = new Thickness(2), Child = sp });
        return count;
    }

    public void UpdateData(ClassifiedItems? c)
    {
        if (c == null) return;
        _cToday.Text = c.Today.Count.ToString();
        _cTomorrow.Text = c.Tomorrow.Count.ToString();
        _cWeek.Text = c.ThisWeek.Count.ToString();
        _cOverdue.Text = c.Overdue.Count.ToString();
        LblFooter.Text = $"Atualizado às {DateTime.Now:HH:mm:ss}";

        List.Children.Clear();
        void Section(string title, string color, IEnumerable<Item> items)
        {
            var list = items.ToList();
            if (list.Count == 0) return;
            List.Children.Add(new TextBlock { Text = title, FontSize = 10, FontWeight = FontWeights.Bold, Foreground = Theme.Brush(color), Margin = new Thickness(4, 8, 4, 2) });
            foreach (var it in list) List.Children.Add(Row(it));
        }
        Section("🚨 VENCENDO HOJE", Formatter.UrgencyColor(0, dark: true), c.Today);
        Section("⚠️ AMANHÃ / PRÓXIMOS DIAS", Formatter.UrgencyColor(1.5, dark: true), c.Tomorrow);
        Section("📅 ESTA SEMANA", Formatter.UrgencyColor(5, dark: true), c.ThisWeek);
        Section("⏳ ATRASADAS / PENDENTES", Formatter.UrgencyColor(-1, dark: true), c.Overdue.Take(8));
        if (List.Children.Count == 0)
            List.Children.Add(new TextBlock { Text = "Nenhum prazo pendente.", FontSize = 11, Foreground = Theme.Brush("#71717a"), HorizontalAlignment = HorizontalAlignment.Center, Margin = new Thickness(20) });
    }

    static Border Row(Item it)
    {
        var top = new DockPanel();
        var tone = Theme.Brush(Formatter.UrgencyColor(it, dark: true));
        var date = new TextBlock { Text = it.DeadlineStr, FontSize = 9, FontWeight = FontWeights.SemiBold, Foreground = tone };
        DockPanel.SetDock(date, Dock.Right);
        top.Children.Add(date);
        top.Children.Add(new TextBlock { Text = $"[{it.Type}]", FontSize = 9, FontWeight = FontWeights.Bold, Foreground = Theme.Brush(it.IsTask ? "#fbbf24" : "#93c5fd") });
        var sp = new StackPanel { Margin = new Thickness(6, 4, 6, 4) };
        sp.Children.Add(top);
        sp.Children.Add(new TextBlock { Text = it.Title, FontSize = 11, FontWeight = FontWeights.Bold, Foreground = tone, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 1, 0, 0) });
        return new Border { Background = Theme.Brush("#1c1c20"), CornerRadius = new CornerRadius(6), Margin = new Thickness(2), Child = sp, ToolTip = it.Description is { Length: > 0 } d ? d[..Math.Min(d.Length, 400)] : null };
    }

    // --- Arraste ---
    void OnHeaderDown(object sender, MouseButtonEventArgs e)
    {
        if (e.OriginalSource is DependencyObject d && FindParent<Button>(d) != null) return;
        DragMove(); // síncrono: volta quando o botão é solto
        SavePosition();
        SendToBottom();
    }

    static T? FindParent<T>(DependencyObject? d) where T : DependencyObject
    {
        for (; d != null; d = VisualTreeHelper.GetParent(d)) if (d is T t) return t;
        return null;
    }

    void SavePosition()
    {
        if (_hwnd != IntPtr.Zero && GetWindowRect(_hwnd, out var r))
        {
            _config.FenceX = r.Left;
            _config.FenceY = r.Top;
        }
    }

    // --- Redimensionamento pela alça ⤡ ---
    void OnResize(object sender, DragDeltaEventArgs e)
    {
        Width = Math.Clamp(Width + e.HorizontalChange, MinW, MaxW);
        if (!_collapsed) Height = Math.Clamp(Height + e.VerticalChange, MinH, MaxH);
    }

    void OnResizeDone(object sender, DragCompletedEventArgs e)
    {
        _config.FenceW = (int)Width;
        if (!_collapsed) _config.FenceH = (int)Height;
        SavePosition();
        SendToBottom();
    }

    /// <summary>Usado pelos sliders da aba Configurações.</summary>
    public void SetDimensions(int w, int h)
    {
        Width = Math.Clamp(w, MinW, MaxW);
        _config.FenceW = (int)Width;
        _config.FenceH = Math.Clamp(h, MinH, MaxH);
        if (!_collapsed) Height = _config.FenceH;
    }

    public void SetOpacity(double alpha)
    {
        _config.FenceAlpha = alpha;
        Opacity = _config.FenceAlpha;
    }

    // --- Recolher ---
    void OnCollapse(object sender, RoutedEventArgs e) => SetCollapsed(!_collapsed, save: true);

    void SetCollapsed(bool collapsed, bool save)
    {
        _collapsed = collapsed;
        Body.Visibility = Footer.Visibility = collapsed ? Visibility.Collapsed : Visibility.Visible;
        BtnCollapse.Content = collapsed ? "▸" : "▾";
        MinHeight = collapsed ? CollapsedH : MinH;
        Height = collapsed ? CollapsedH : Math.Clamp(_config.FenceH, MinH, MaxH);
        if (save) _config.FenceCollapsed = collapsed;
    }

    void OnSync(object sender, RoutedEventArgs e) => _ = _scheduler.SyncAndNotify(force: true);

    public void ShowWidget()
    {
        _config.DesktopFenceEnabled = true;
        Show();
        Pin();
    }

    public void HideWidget()
    {
        _config.DesktopFenceEnabled = false;
        Hide();
    }
}
