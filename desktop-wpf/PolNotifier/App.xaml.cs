using System.Windows;
using System.Windows.Threading;
using PolNotifier.Core;
using PolNotifier.Ui;
using Forms = System.Windows.Forms;

namespace PolNotifier;

/// <summary>Fluxo do app (app/main.py): login → sessão do usuário (banco, agendador, widget, painel, bandeja).
/// "Trocar usuário" desmonta a sessão e volta ao login sem auto-login.</summary>
public partial class App : Application
{
    Mutex? _mutex;
    Database _appDb = null!;
    Session? _session;

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        // Instância única: abrir o .exe de novo só pede para a instância aberta mostrar o painel
        var showSignal = new EventWaitHandle(false, EventResetMode.AutoReset, @"Local\POL_ADV_Notifier_Show");
        _mutex = new Mutex(true, @"Local\POL_ADV_Notifier_Desktop", out var first);
        if (!first)
        {
            showSignal.Set();
            Shutdown();
            return;
        }
        new Thread(() => { while (showSignal.WaitOne()) Dispatcher.BeginInvoke(() => _session?.ShowPanel()); })
            { IsBackground = true }.Start();
        DispatcherUnhandledException += OnUnhandled;
        Theme.Apply(Resources);
        // Banco global: só guarda qual e-mail foi lembrado; o resto é por usuário
        _appDb = new Database(Database.GlobalPath);
        await Login(auto: true);
    }

    void OnUnhandled(object sender, DispatcherUnhandledExceptionEventArgs e)
    {
        (_session?.Db ?? _appDb)?.Log("ERROR", $"Erro inesperado: {e.Exception}");
        Alert.Show(null, "Erro", $"Erro inesperado: {e.Exception.Message}", isError: true);
        e.Handled = true;
    }

    /// <summary>Com credenciais lembradas e auto, entra sem mostrar a tela.</summary>
    async Task Login(bool auto)
    {
        var error = "";
        if (auto && LoginWindow.LoadRemembered(_appDb) is var (email, pass))
        {
            try
            {
                await StartSession(new(await PolClient.KeycloakLogin(email, pass), email, pass));
                return;
            }
            catch (LoginException ex) { error = ex.Message; }
        }
        var win = new LoginWindow(_appDb, error);
        win.ShowDialog();
        if (win.Result is { } r) await StartSession(r);
        else Shutdown();
    }

    async Task StartSession(LoginResult r)
    {
        var claims = PolClient.JwtClaims(r.Tokens.AccessToken);
        var db = Database.OpenUser((string)claims["sub"]!, r.Email);
        var config = new Config(db);
        var pol = new PolClient(config);
        await pol.StartSession(r.Tokens, r.Email, r.Password);
        var name = (string?)claims["name"] is { Length: > 0 } n ? n : r.Email.Split('@')[0];
        _session = new Session(this, db, config, pol, name);
    }

    async void Logout()
    {
        _session?.Dispose();
        _session = null;
        await Login(auto: false);
    }

    void Quit()
    {
        _session?.Dispose();
        _session = null;
        Shutdown();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _session?.Dispose();
        _mutex?.Dispose();
        base.OnExit(e);
    }

    /// <summary>Tudo o que pertence ao usuário logado.</summary>
    sealed class Session : IDisposable
    {
        public Database Db { get; }
        readonly Scheduler _scheduler;
        readonly FenceWindow _fence;
        readonly MainWindow _main;
        readonly Forms.NotifyIcon _tray;

        public Session(App app, Database db, Config config, PolClient pol, string userName)
        {
            Db = db;
            _scheduler = new Scheduler(config, pol);
            _fence = new FenceWindow(config, _scheduler);
            _main = new MainWindow(config, pol, _scheduler, _fence, userName);
            _main.QuitRequested += app.Quit;
            _main.LogoutRequested += app.Logout;
            // Mostra os itens em cache (sem sincronizar) até o primeiro ciclo terminar
            _fence.UpdateData(_main.LoadCached());

            var ui = app.Dispatcher;
            _scheduler.SyncCompleted += c => ui.BeginInvoke(() =>
            {
                _fence.UpdateData(c);
                _main.UpdateData(c);
            });
            _scheduler.StatusChanged += s => ui.BeginInvoke(() => _main.SetStatus(s.StartsWith("Erro") ? $"🔴 {s}" : $"🟢 {s}",
                s.StartsWith("Erro") ? "#ef4444" : "#10b981"));

            if (config.DesktopFenceEnabled) _fence.Show();
            if (!config.StartMinimized) _main.Show();

            var menu = new Forms.ContextMenuStrip();
            menu.Items.Add("Abrir Painel POL ADV", null, (_, _) => ui.BeginInvoke(_main.ShowFromTray)).Font =
                new System.Drawing.Font(menu.Font, System.Drawing.FontStyle.Bold);
            menu.Items.Add("Sincronizar Prazos Agora", null, (_, _) => _ = _scheduler.SyncAndNotify(force: true));
            menu.Items.Add("🖥️ Exibir/Ocultar Widget Desktop", null, (_, _) => ui.BeginInvoke(() =>
            {
                if (_fence.IsVisible) _fence.HideWidget(); else _fence.ShowWidget();
                _main.UpdateFenceButton();
            }));
            menu.Items.Add(new Forms.ToolStripSeparator());
            menu.Items.Add("👤 Trocar Usuário", null, (_, _) => ui.BeginInvoke(app.Logout));
            menu.Items.Add("Encerrar Aplicação", null, (_, _) => ui.BeginInvoke(app.Quit));
            _tray = new Forms.NotifyIcon
            {
                Icon = System.Drawing.Icon.ExtractAssociatedIcon(Environment.ProcessPath!),
                Text = "POL ADV — Notificador de Prazos",
                ContextMenuStrip = menu,
                Visible = true,
            };
            _tray.MouseClick += (_, e) => { if (e.Button == Forms.MouseButtons.Left) ui.BeginInvoke(_main.ShowFromTray); };

            _scheduler.Start();
        }

        public void ShowPanel() => _main.ShowFromTray();

        public void Dispose()
        {
            // Uma sincronização em andamento não pode tocar a UI destruída (Stop limpa os eventos)
            _scheduler.Stop();
            _tray.Visible = false;
            _tray.Dispose();
            _main.AllowClose = true;
            _main.Close();
            _fence.Close();
        }
    }
}
