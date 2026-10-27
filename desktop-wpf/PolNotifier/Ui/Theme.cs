using System.Windows;
using System.Windows.Media;
using Microsoft.Win32;

namespace PolNotifier.Ui;

/// <summary>Cor de destaque do Windows (DWM AccentColor = 0xAABBGGRR), como app/ui/theme.py.</summary>
public static class Theme
{
    public static Color Accent { get; } = ReadAccent();

    static Color ReadAccent()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\DWM");
            if (key?.GetValue("AccentColor") is int a)
                return Color.FromRgb((byte)(a & 0xFF), (byte)((a >> 8) & 0xFF), (byte)((a >> 16) & 0xFF));
            if (key?.GetValue("ColorizationColor") is int c) // 0xAARRGGBB
                return Color.FromRgb((byte)((c >> 16) & 0xFF), (byte)((c >> 8) & 0xFF), (byte)(c & 0xFF));
        }
        catch (System.Security.SecurityException) { }
        return Color.FromRgb(0x00, 0x78, 0xd4); // azul padrão do Windows
    }

    public static string AccentHex => $"#{Accent.R:x2}{Accent.G:x2}{Accent.B:x2}";

    public static SolidColorBrush Brush(string hex) => new((Color)ColorConverter.ConvertFromString(hex));

    public static void Apply(ResourceDictionary res)
    {
        res["AccentBrush"] = new SolidColorBrush(Accent);
        res["AccentHoverBrush"] = new SolidColorBrush(Color.FromRgb((byte)(Accent.R * .85), (byte)(Accent.G * .85), (byte)(Accent.B * .85)));
    }
}
