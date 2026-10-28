using System.IO;
using Microsoft.Data.Sqlite;

namespace PolNotifier.Core;

/// <summary>Mesmo esquema de app/database.py: os bancos são compartilhados com a versão Python.</summary>
public sealed class Database
{
    // Chaves do banco global que não pertencem a nenhum usuário
    static readonly string[] GlobalKeys = ["remembered_email", "migrated_to"];

    /// <summary>Pasta data/ do projeto: sobe a partir do .exe até achar data/pol_notifier.db
    /// (rodando de desktop-wpf/bin/... cai em Z:\pol\data). Sem achar (app instalado), usa
    /// %LOCALAPPDATA%\POL ADV Notifier\data. POL_DATA_DIR no ambiente tem precedência.</summary>
    public static readonly string DataDir = ResolveDataDir();

    static string ResolveDataDir()
    {
        var env = Environment.GetEnvironmentVariable("POL_DATA_DIR");
        if (!string.IsNullOrWhiteSpace(env)) return Path.GetFullPath(env);
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
        {
            var candidate = Path.Combine(dir.FullName, "data");
            if (File.Exists(Path.Combine(candidate, "pol_notifier.db"))) return candidate;
        }
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "POL ADV Notifier", "data");
    }

    public static string GlobalPath => Path.Combine(DataDir, "pol_notifier.db");
    static string UsersDir => Path.Combine(DataDir, "users");

    readonly string _connStr;

    public Database(string path)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        _connStr = new SqliteConnectionStringBuilder { DataSource = path, DefaultTimeout = 10 }.ToString();
        InitTables();
    }

    /// <summary>Abre data/users/&lt;sub&gt;.db. Na primeira vez migra o banco legado global se ele for
    /// desse usuário (mesmo pol_user_id ou pol_email), como open_user_db() do Python.</summary>
    public static Database OpenUser(string sub, string email)
    {
        var path = Path.Combine(UsersDir, $"{sub}.db");
        if (!File.Exists(path) && File.Exists(GlobalPath))
        {
            var legacy = new Database(GlobalPath);
            var ownerId = legacy.GetSetting("pol_user_id", "");
            var ownerEmail = legacy.GetSetting("pol_email", "")!.ToLowerInvariant();
            if (string.IsNullOrEmpty(legacy.GetSetting("migrated_to")) && (ownerId == sub || ownerEmail == email.ToLowerInvariant()))
            {
                Directory.CreateDirectory(UsersDir);
                using (var src = legacy.Open())
                using (var dst = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = path }.ToString()))
                {
                    dst.Open();
                    src.BackupDatabase(dst);
                }
                legacy.Exec($"DELETE FROM settings WHERE key NOT IN ('{string.Join("','", GlobalKeys)}')");
                legacy.Exec("DELETE FROM cached_items");
                legacy.Exec("DELETE FROM notification_history");
                legacy.Exec("VACUUM"); // não deixar a senha legada em páginas livres
                legacy.SetSetting("migrated_to", sub);
                legacy.Log("INFO", $"Dados legados migrados para o usuário {email}.");
            }
        }
        var db = new Database(path);
        // Senha e tokens nunca ficam no SQLite (chaves globais podem ter vindo na cópia do legado)
        var stale = new[] { "pol_password", "pol_access_token", "pol_refresh_token", "pol_token_expiry" }.Concat(GlobalKeys)
            .Count(db.DeleteSetting);
        if (stale > 0) db.Exec("VACUUM");
        return db;
    }

    SqliteConnection Open()
    {
        var c = new SqliteConnection(_connStr);
        c.Open();
        return c;
    }

    int Exec(string sql, params (string, object?)[] args)
    {
        using var c = Open();
        using var cmd = c.CreateCommand();
        cmd.CommandText = sql;
        foreach (var (k, v) in args) cmd.Parameters.AddWithValue(k, v ?? DBNull.Value);
        return cmd.ExecuteNonQuery();
    }

    List<Dictionary<string, object?>> Query(string sql, params (string, object?)[] args)
    {
        using var c = Open();
        using var cmd = c.CreateCommand();
        cmd.CommandText = sql;
        foreach (var (k, v) in args) cmd.Parameters.AddWithValue(k, v ?? DBNull.Value);
        using var r = cmd.ExecuteReader();
        var rows = new List<Dictionary<string, object?>>();
        while (r.Read())
        {
            var row = new Dictionary<string, object?>();
            for (int i = 0; i < r.FieldCount; i++) row[r.GetName(i)] = r.IsDBNull(i) ? null : r.GetValue(i);
            rows.Add(row);
        }
        return rows;
    }

    void InitTables()
    {
        Exec("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS cached_items (
                id TEXT PRIMARY KEY,
                item_type TEXT,
                title TEXT,
                description TEXT,
                status TEXT,
                priority TEXT,
                deadline TIMESTAMP,
                raw_json TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS notification_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT,
                channel TEXT,
                section TEXT,
                recipient TEXT,
                sent_date TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT,
                error_message TEXT
            );
            CREATE TABLE IF NOT EXISTS app_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT,
                message TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """);
    }

    // --- Configurações ---
    public string? GetSetting(string key, string? def = null) =>
        Query("SELECT value FROM settings WHERE key = $k", ("$k", key)).FirstOrDefault()?["value"] as string ?? def;

    public void SetSetting(string key, string? value) => Exec("""
        INSERT INTO settings (key, value, updated_at) VALUES ($k, $v, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """, ("$k", key), ("$v", value ?? ""));

    public bool DeleteSetting(string key) => Exec("DELETE FROM settings WHERE key = $k", ("$k", key)) > 0;

    // --- Cache de itens ---
    public void UpsertCachedItem(string id, string type, string? title, string? description, string? status,
                                 string? priority, string? deadline, string rawJson) => Exec("""
        INSERT INTO cached_items (id, item_type, title, description, status, priority, deadline, raw_json, updated_at)
        VALUES ($id, $t, $ti, $d, $s, $p, $dl, $raw, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title, description = excluded.description, status = excluded.status,
            priority = excluded.priority, deadline = excluded.deadline, raw_json = excluded.raw_json,
            updated_at = CURRENT_TIMESTAMP
        """, ("$id", id), ("$t", type), ("$ti", title), ("$d", description), ("$s", status),
             ("$p", priority), ("$dl", deadline), ("$raw", rawJson));

    public List<(string Type, string RawJson)> GetCachedItems() =>
        Query("SELECT item_type, raw_json FROM cached_items ORDER BY deadline ASC")
            .Select(r => ((string)r["item_type"]!, r["raw_json"] as string ?? "{}")).ToList();

    public void ClearCachedItems() => Exec("DELETE FROM cached_items");

    // --- Deduplicação de notificações ---
    public bool IsAlreadyNotified(string itemId, string channel, string section, string sentDate) =>
        Query("""
            SELECT id FROM notification_history
            WHERE item_id = $i AND channel = $c AND section = $s AND sent_date = $d AND status = 'SUCCESS'
            """, ("$i", itemId), ("$c", channel), ("$s", section), ("$d", sentDate)).Count > 0;

    public void RecordNotification(string itemId, string channel, string section, string recipient,
                                   string sentDate, string status, string error) => Exec("""
        INSERT INTO notification_history (item_id, channel, section, recipient, sent_date, status, error_message)
        VALUES ($i, $c, $s, $r, $d, $st, $e)
        """, ("$i", itemId), ("$c", channel), ("$s", section), ("$r", recipient), ("$d", sentDate),
             ("$st", status), ("$e", error));

    public List<Dictionary<string, object?>> GetRecentNotifications(int limit = 50) =>
        Query("SELECT * FROM notification_history ORDER BY sent_at DESC LIMIT $l", ("$l", limit));

    // --- Logs ---
    public void Log(string level, string message)
    {
        try { Exec("INSERT INTO app_logs (level, message, timestamp) VALUES ($l, $m, CURRENT_TIMESTAMP)",
                   ("$l", level.ToUpperInvariant()), ("$m", message)); }
        catch (SqliteException) { } // log nunca derruba o app
    }

    public List<Dictionary<string, object?>> GetLogs(int limit = 100) =>
        Query("SELECT * FROM app_logs ORDER BY timestamp DESC, id DESC LIMIT $l", ("$l", limit));

    public void ClearLogs() => Exec("DELETE FROM app_logs");
}
