using System.Windows;
using PolNotifier.Core;

namespace PolNotifier.Ui;

public sealed record LoginResult(Tokens Tokens, string Email, string Password);

/// <summary>Tela de login (app/ui/login_window.py). Senha lembrada vai para o Credential Manager.</summary>
public partial class LoginWindow : Window
{
    readonly Database _appDb;
    public LoginResult? Result { get; private set; }

    public LoginWindow(Database appDb, string error = "")
    {
        InitializeComponent();
        _appDb = appDb;
        LblError.Text = error;
        if (LoadRemembered(appDb) is var (email, pass))
        {
            TxtEmail.Text = email;
            TxtPass.Password = pass;
            ChkRemember.IsChecked = true;
            Loaded += (_, _) => TxtPass.Focus();
        }
        else Loaded += (_, _) => TxtEmail.Focus();
    }

    public static (string Email, string Password)? LoadRemembered(Database appDb)
    {
        var email = appDb.GetSetting("remembered_email", "") ?? "";
        if (email == "") return null;
        try { return CredentialStore.GetPassword(email) is { } p ? (email, p) : null; }
        catch (Exception) { return null; }
    }

    static void SaveRemembered(Database appDb, string email, string password, bool remember)
    {
        try
        {
            if (remember)
            {
                CredentialStore.SetPassword(email, password);
                appDb.SetSetting("remembered_email", email);
                return;
            }
            CredentialStore.DeletePassword(email);
        }
        catch (Exception e) { appDb.Log("WARNING", $"Credential Manager indisponível: {e.Message}"); }
        if (string.Equals(appDb.GetSetting("remembered_email", ""), email, StringComparison.OrdinalIgnoreCase))
            appDb.DeleteSetting("remembered_email");
    }

    async void OnLogin(object sender, RoutedEventArgs e)
    {
        var email = TxtEmail.Text.Trim();
        var password = TxtPass.Password;
        if (email == "" || password == "")
        {
            LblError.Text = "Informe e-mail e senha.";
            return;
        }
        LblError.Text = "";
        BtnLogin.IsEnabled = false;
        BtnLogin.Content = "Entrando...";
        try
        {
            var tokens = await PolClient.KeycloakLogin(email, password);
            SaveRemembered(_appDb, email, password, ChkRemember.IsChecked == true);
            Result = new(tokens, email, password);
            Close();
        }
        catch (LoginException ex)
        {
            LblError.Text = ex.Message;
            BtnLogin.IsEnabled = true;
            BtnLogin.Content = "Entrar";
            TxtPass.Focus();
        }
    }
}
