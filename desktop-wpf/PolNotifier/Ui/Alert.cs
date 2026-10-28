using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace PolNotifier.Ui;

/// <summary>Modal informativo simples (equivalente ao ModernAlert do Python).</summary>
public static class Alert
{
    public static void Show(Window? owner, string title, string message, bool isError = false, bool isWarning = false)
    {
        var low = (title + " " + message).ToLowerInvariant();
        var (icon, color) = isError || title.ToLowerInvariant().Contains("erro")
            ? ("❌", "#ef4444")
            : isWarning ? ("⚠️", "#f59e0b")
            : low.Contains("sucesso") ? ("✅", "#10b981") : ("ℹ️", "#3b82f6");

        var ok = new Button
        {
            Content = "Entendido", Style = (Style)Application.Current.Resources["FlatButton"], Background = Theme.Brush(color),
            HorizontalAlignment = HorizontalAlignment.Right, IsDefault = true, IsCancel = true, MinWidth = 100,
        };
        var panel = new StackPanel { Margin = new Thickness(20) };
        panel.Children.Add(new TextBlock { Text = $"{icon} {title}", FontSize = 15, FontWeight = FontWeights.Bold, Foreground = Theme.Brush(color) });
        panel.Children.Add(new TextBlock { Text = message, FontSize = 12, Foreground = Theme.Brush("#e4e4e7"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 10, 0, 18) });
        panel.Children.Add(ok);

        var win = new Window
        {
            Title = title, Content = panel, Width = 440, SizeToContent = SizeToContent.Height, ResizeMode = ResizeMode.NoResize,
            Background = Theme.Brush("#18181b"), ShowInTaskbar = owner is not { IsVisible: true },
            WindowStartupLocation = owner is { IsVisible: true } ? WindowStartupLocation.CenterOwner : WindowStartupLocation.CenterScreen,
        };
        if (owner is { IsVisible: true }) win.Owner = owner;
        ok.Click += (_, _) => win.Close();
        win.ShowDialog();
    }
}
