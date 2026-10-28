using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using PolNotifier.Core;

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

    public static string StateOf(BoardItem b) => b.IsTask ? (b.IsDone ? "Concluída" : b.IsOverdue ? "Atrasada" : "Pendente") : "Compromisso";

    public static string WhenOf(BoardItem b)
    {
        if (b.Deadline is not { } d) return "Sem prazo";
        var weekday = d.ToString("dddd", System.Globalization.CultureInfo.GetCultureInfo("pt-BR"));
        if (b.IsTask) return d.TimeOfDay >= new TimeSpan(23, 59, 0) ? $"{d:dd/MM/yyyy} ({weekday})" : $"{d:dd/MM/yyyy HH:mm} ({weekday})";
        return b.End is { } e && e > d ? $"{d:dd/MM/yyyy HH:mm} até {(e.Date == d.Date ? e.ToString("HH:mm") : e.ToString("dd/MM/yyyy HH:mm"))} ({weekday})" : $"{d:dd/MM/yyyy HH:mm} ({weekday})";
    }

    /// <summary>Popup com todos os detalhes de uma tarefa ou compromisso (clique no calendário/Kanban).</summary>
    public static void Detail(Window? owner, BoardItem b)
    {
        var panel = new StackPanel { Margin = new Thickness(22) };
        panel.Children.Add(new TextBlock { Text = b.Title, FontSize = 16, FontWeight = FontWeights.Bold, Foreground = Theme.Brush("#f4f4f5"), TextWrapping = TextWrapping.Wrap });
        panel.Children.Add(new Border
        {
            Background = Theme.Brush(b.CalendarColor), CornerRadius = new CornerRadius(4), Padding = new Thickness(8, 2, 8, 3), Margin = new Thickness(0, 10, 0, 12),
            HorizontalAlignment = HorizontalAlignment.Left,
            Child = new TextBlock { Text = StateOf(b), FontSize = 11, FontWeight = FontWeights.Bold, Foreground = Brushes.White },
        });

        void Field(string label, string value)
        {
            if (value == "") return;
            var g = new Grid { Margin = new Thickness(0, 0, 0, 5) };
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(110) });
            g.ColumnDefinitions.Add(new ColumnDefinition());
            g.Children.Add(new TextBlock { Text = label, FontSize = 12, Foreground = Theme.Brush("#a1a1aa") });
            var v = new TextBlock { Text = value, FontSize = 12.5, Foreground = Theme.Brush("#e4e4e7"), TextWrapping = TextWrapping.Wrap };
            Grid.SetColumn(v, 1);
            g.Children.Add(v);
            panel.Children.Add(g);
        }
        Field(b.IsTask ? "Prazo" : "Data", WhenOf(b));
        if (b.IsTask && Board.Priorities.TryGetValue(b.Priority, out var pr)) Field("Prioridade", pr.Label);
        if (b.IsTask) Field("Situação", b.Status switch { "TO_DO" => "A fazer", "IN_PROGRESS" => "Em andamento", "REVIEW" => "A revisar", "DONE" => "Concluída", _ => b.Status });
        Field("Categoria", b.Category);
        Field("Responsável", b.Assignee);

        if (b.Description != "")
        {
            panel.Children.Add(new TextBlock { Text = "Descrição", FontSize = 12, Foreground = Theme.Brush("#a1a1aa"), Margin = new Thickness(0, 10, 0, 4) });
            panel.Children.Add(new Border
            {
                Background = Theme.Brush("#121215"), CornerRadius = new CornerRadius(8),
                Child = new TextBox
                {
                    Text = b.Description, IsReadOnly = true, TextWrapping = TextWrapping.Wrap, VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                    MaxHeight = 300, Background = Brushes.Transparent, BorderThickness = new Thickness(0), Foreground = Theme.Brush("#d4d4d8"), Padding = new Thickness(10), FontSize = 12.5,
                },
            });
        }
        var close = new Button
        {
            Content = "Fechar", Style = (Style)Application.Current.Resources["FlatButton"], HorizontalAlignment = HorizontalAlignment.Right,
            MinWidth = 100, IsDefault = true, IsCancel = true, Margin = new Thickness(0, 16, 0, 0),
        };
        panel.Children.Add(close);

        var win = new Window
        {
            Title = StateOf(b), Content = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = panel },
            Width = 580, SizeToContent = SizeToContent.Height, MaxHeight = 700, ResizeMode = ResizeMode.NoResize, Background = Theme.Brush("#18181b"),
            ShowInTaskbar = owner is not { IsVisible: true },
            WindowStartupLocation = owner is { IsVisible: true } ? WindowStartupLocation.CenterOwner : WindowStartupLocation.CenterScreen,
        };
        if (owner is { IsVisible: true }) win.Owner = owner;
        close.Click += (_, _) => win.Close();
        win.ShowDialog();
    }
}
