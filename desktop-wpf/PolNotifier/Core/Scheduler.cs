namespace PolNotifier.Core;

/// <summary>Sincronização em segundo plano e envio consolidado com deduplicação diária por canal
/// (app/scheduler.py). Eventos chegam fora da thread de UI.</summary>
public sealed class Scheduler(Config config, PolClient pol)
{
    readonly Database _db = config.Db;
    readonly Notifiers _notifiers = new(config);
    readonly AutoResetEvent _wake = new(false);
    readonly SemaphoreSlim _syncLock = new(1, 1); // sincronização manual e do ciclo não se sobrepõem
    volatile bool _running;

    public ClassifiedItems? LastClassified { get; private set; }
    public DateTime? LastSync { get; private set; }
    public event Action<ClassifiedItems>? SyncCompleted;
    public event Action<string>? StatusChanged;
    public Notifiers Notifiers => _notifiers;
    /// <summary>Nome do usuário (claim "name" do JWT), usado na saudação do e-mail.</summary>
    public string UserName { get; set; } = "";
    public string FirstName => UserName.Split(' ', StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? "";

    public void Start()
    {
        if (_running) return;
        _running = true;
        new Thread(Worker) { IsBackground = true, Name = "SchedulerWorker" }.Start();
        _db.Log("INFO", "Agendador de monitoramento iniciado em segundo plano.");
    }

    public void Stop()
    {
        _running = false;
        SyncCompleted = null;
        StatusChanged = null;
        _wake.Set();
    }

    /// <summary>Acorda o ciclo para aplicar um novo intervalo.</summary>
    public void RestartCycle() => _wake.Set();

    void Worker()
    {
        Thread.Sleep(2000);
        if (_running) SyncAndNotify().Wait();
        while (_running)
        {
            var interval = TimeSpan.FromMinutes(Math.Max(1, config.CheckIntervalMinutes));
            // Acordado antes do tempo = intervalo mudou (ou parada): só recalcula a espera
            if (!_wake.WaitOne(interval) && _running) SyncAndNotify().Wait();
        }
    }

    public async Task<ClassifiedItems> SyncAndNotify(bool force = false)
    {
        await _syncLock.WaitAsync();
        try
        {
            StatusChanged?.Invoke("Sincronizando com POL ADV...");
            _db.Log("INFO", $"Iniciando ciclo de sincronização (force={force})...");

            var tasks = await pol.GetTasks();
            var events = await pol.GetEvents();
            var c = Formatter.Classify(tasks, events);
            LastClassified = c;
            LastSync = DateTime.Now;

            if (c.HasItems) await SendReport(c, force);

            StatusChanged?.Invoke($"Atualizado em {LastSync:HH:mm:ss}");
            SyncCompleted?.Invoke(c);
            return c;
        }
        catch (Exception e)
        {
            _db.Log("ERROR", $"Falha no ciclo de sincronização: {e.Message}");
            StatusChanged?.Invoke("Erro na sincronização");
            return LastClassified ?? new ClassifiedItems();
        }
        finally { _syncLock.Release(); }
    }
    /// <summary>Envia o relatório por canal habilitado. Sem `force`, só manda num canal se algum item
    /// ainda não foi avisado hoje nele (deduplicação diária). Devolve o resultado de cada canal.</summary>
    async Task<List<string>> SendReport(ClassifiedItems c, bool force)
    {
        var results = new List<string>();
        var items = c.All.ToList();
        var today = DateTime.Today.ToString("yyyy-MM-dd");

        async Task Channel(bool enabled, string channel, string label, IEnumerable<string> recipients, Func<Task<(bool, string)>> send)
        {
            if (!enabled) { results.Add($"{label}: desativado"); return; }
            if (!force && items.All(it => _db.IsAlreadyNotified(it.Id, channel, "CONSOLIDATED", today))) return;
            var (ok, msg) = await send();
            results.Add($"{label}: {msg}");
            var rcpt = string.Join(", ", recipients);
            foreach (var it in items)
                _db.RecordNotification(it.Id, channel, "CONSOLIDATED", rcpt, today, ok ? "SUCCESS" : "ERROR", msg);
        }

        await Channel(config.EmailEnabled, "EMAIL", "E-mail", config.SmtpRecipients, () => _notifiers.SendEmail(
            Formatter.EmailSubject(c), Formatter.ToHtml(c, FirstName)));
        await Channel(config.TelegramEnabled, "TELEGRAM", "Telegram", config.TelegramChatIds,
            () => _notifiers.SendTelegram(Formatter.ToTelegramMarkdown(c)));
        await Channel(config.OpenWaEnabled, "OPENWA", "WhatsApp", config.OpenWaRecipients,
            () => _notifiers.SendOpenWa(Formatter.ToOpenWaText(c)));
        return results;
    }

    /// <summary>Botão "Enviar último relatório": reenvia o último relatório sincronizado (sincroniza
    /// antes se ainda não houver) para todos os canais habilitados, ignorando a deduplicação.</summary>
    public async Task<List<string>> SendLastReport()
    {
        var c = LastClassified ?? await SyncAndNotify();
        _db.Log("INFO", "Envio manual do último relatório.");
        return await SendReport(c, force: true);
    }
}
