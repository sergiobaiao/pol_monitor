using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace PolNotifier.Core;

public sealed class LoginException(string message) : Exception(message);

public sealed record Tokens(string AccessToken, string? RefreshToken, int ExpiresIn);

/// <summary>Cliente do POL ADV (Keycloak + gateway), como app/pol_client.py.</summary>
public sealed class PolClient(Config config)
{
    const string KeycloakTokenUrl = "https://keycloak.pol.adv.br/auth/realms/pol-prod/protocol/openid-connect/token";
    const string GatewayUrl = "https://office-api.pol.adv.br";

    public static readonly HttpClient Http = CreateHttp();

    static HttpClient CreateHttp()
    {
        var h = new HttpClient { Timeout = TimeSpan.FromSeconds(15) };
        // O nginx do gateway responde 403 a requisições sem User-Agent (o HttpClient não envia nenhum)
        h.DefaultRequestHeaders.UserAgent.ParseAdd("PolNotifier/2.0");
        return h;
    }

    readonly Database _db = config.Db;
    readonly SemaphoreSlim _authLock = new(1, 1);
    // Tokens e senhas ficam só em memória; o login acontece a cada abertura do app
    string? _accessToken, _refreshToken;
    DateTime _expiry;
    // O login do app identifica o usuário/banco; a conta da API é quem de fato é consultado no POL.
    // Por padrão são a mesma, mas podem ser diferentes (Configurações → Conta da API).
    string _loginEmail = "", _loginPassword = "", _activeUser = "", _activePassword = "", _apiSub = "";
    public const string ApiCredentialService = "POL ADV Notifier API";

    /// <summary>Usuário cuja conta a API está usando agora.</summary>
    public string ApiUser => _activeUser;
    public bool UsesLoginAccount => string.Equals(_activeUser, _loginEmail, StringComparison.OrdinalIgnoreCase);

    static async Task<HttpResponseMessage> PostToken(Dictionary<string, string> form) =>
        await Http.PostAsync(KeycloakTokenUrl, new FormUrlEncodedContent(form));

    static Tokens ParseTokens(string json)
    {
        var o = JsonNode.Parse(json)!;
        return new((string)o["access_token"]!, (string?)o["refresh_token"], (int?)o["expires_in"] ?? 300);
    }

    /// <summary>Valida e-mail/senha direto no Keycloak (grant_type=password).</summary>
    public static async Task<Tokens> KeycloakLogin(string email, string password)
    {
        HttpResponseMessage resp;
        try
        {
            resp = await PostToken(new() { ["client_id"] = "web_app", ["grant_type"] = "password",
                                           ["username"] = email, ["password"] = password });
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException)
        {
            throw new LoginException($"Não foi possível conectar ao POL ADV: {e.Message}");
        }
        var body = await resp.Content.ReadAsStringAsync();
        if (resp.IsSuccessStatusCode) return ParseTokens(body);
        if (resp.StatusCode == HttpStatusCode.Unauthorized) throw new LoginException("E-mail ou senha incorretos.");
        string detail = body;
        try { detail = (string?)JsonNode.Parse(body)?["error_description"] ?? body; } catch (JsonException) { }
        throw new LoginException($"O POL ADV recusou o login (HTTP {(int)resp.StatusCode}): {detail}");
    }

    /// <summary>Payload do JWT (sem validar assinatura: veio direto do Keycloak via HTTPS).</summary>
    public static JsonNode JwtClaims(string token)
    {
        var p = token.Split('.')[1].Replace('-', '+').Replace('_', '/');
        p = p.PadRight(p.Length + (4 - p.Length % 4) % 4, '=');
        return JsonNode.Parse(Encoding.UTF8.GetString(Convert.FromBase64String(p)))!;
    }

    public async Task StartSession(Tokens tokens, string email, string password)
    {
        _loginEmail = _activeUser = email;
        _loginPassword = _activePassword = password;
        _refreshToken = null;
        Store(tokens);
        config.PolEmail = email;
        _apiSub = config.PolUserId = (string?)JwtClaims(_accessToken!)["sub"] ?? "";
        _db.Log("INFO", $"Login POL ADV realizado para {email}");
        await ApplySavedApiAccount();
        if (string.IsNullOrEmpty(config.PolOfficeId)) await LoadOffice();
    }

    /// <summary>Se o usuário configurou uma conta de API diferente do login, entra com ela (senha no Credential Manager).</summary>
    async Task ApplySavedApiAccount()
    {
        var api = config.PolApiUser;
        if (api == "" || string.Equals(api, _loginEmail, StringComparison.OrdinalIgnoreCase)) return;
        string? pw = null;
        try { pw = CredentialStore.GetPassword(api, ApiCredentialService); } catch (Exception) { }
        if (pw == null)
        {
            _db.Log("WARNING", $"Senha da conta da API ({api}) não encontrada no Gerenciador de Credenciais; usando a conta do login.");
            return;
        }
        try
        {
            SwitchTo(api, pw, await KeycloakLogin(api, pw));
            _db.Log("INFO", $"Conta da API: {api}");
        }
        catch (LoginException e) { _db.Log("ERROR", $"Conta da API ({api}) recusada ({e.Message}); usando a conta do login."); }
    }

    void SwitchTo(string user, string password, Tokens t)
    {
        _activeUser = user;
        _activePassword = password;
        _refreshToken = null;
        Store(t);
        _apiSub = (string?)JwtClaims(_accessToken!)["sub"] ?? "";
    }

    /// <summary>Define a conta usada na API (pode ser diferente do login). Valida no Keycloak antes de salvar;
    /// senha em branco reaproveita a salva (ou a do login, se for o mesmo usuário). Lança LoginException se recusada.</summary>
    public async Task SetApiAccount(string user, string password)
    {
        user = user.Trim();
        if (user == "") throw new LoginException("Informe o usuário da conta da API.");
        if (string.Equals(user, _loginEmail, StringComparison.OrdinalIgnoreCase) && (password == "" || password == _loginPassword))
        {
            await UseLoginAccount();
            return;
        }
        if (password == "")
        {
            password = (string.Equals(user, _activeUser, StringComparison.OrdinalIgnoreCase) ? _activePassword
                        : CredentialStore.GetPassword(user, ApiCredentialService)) ?? "";
            if (password == "") throw new LoginException("Informe a senha da conta da API.");
        }
        var tokens = await KeycloakLogin(user, password);
        SwitchTo(user, password, tokens);
        config.PolApiUser = user;
        CredentialStore.SetPassword(user, password, ApiCredentialService);
        await AfterAccountChange($"Conta da API alterada para {user}.");
    }

    public async Task UseLoginAccount()
    {
        var old = config.PolApiUser;
        if (UsesLoginAccount && old == "") return;
        SwitchTo(_loginEmail, _loginPassword, await KeycloakLogin(_loginEmail, _loginPassword));
        config.PolApiUser = "";
        if (old != "") { try { CredentialStore.DeletePassword(old, ApiCredentialService); } catch (Exception) { } }
        await AfterAccountChange("A API voltou a usar a conta do login.");
    }

    async Task AfterAccountChange(string message)
    {
        _db.ClearCachedItems(); // o cache era de outra conta
        await LoadOffice();     // o escritório pode ser outro
        _db.Log("INFO", message);
    }

    void Store(Tokens t)
    {
        _accessToken = t.AccessToken;
        _refreshToken = t.RefreshToken ?? _refreshToken;
        _expiry = DateTime.UtcNow.AddSeconds(t.ExpiresIn);
    }

    public async Task<bool> Authenticate()
    {
        await _authLock.WaitAsync();
        try
        {
            // Token válido com mais de 5 minutos de folga
            if (_accessToken != null && DateTime.UtcNow < _expiry.AddMinutes(-5)) return true;
            if (_refreshToken != null && await Refresh()) return true;
            // Refresh expirou: refaz o login com a senha da conta da API em uso
            if (_activePassword == "")
            {
                _db.Log("WARNING", "Sessão POL expirada. Faça login novamente.");
                return false;
            }
            try
            {
                Store(await KeycloakLogin(_activeUser, _activePassword));
                _db.Log("INFO", $"Sessão POL renovada para {_activeUser}");
                return true;
            }
            catch (LoginException e)
            {
                _db.Log("ERROR", $"Falha na autenticação POL: {e.Message}");
                return false;
            }
        }
        finally { _authLock.Release(); }
    }

    async Task<bool> Refresh()
    {
        try
        {
            var resp = await PostToken(new() { ["client_id"] = "web_app", ["grant_type"] = "refresh_token",
                                               ["refresh_token"] = _refreshToken! });
            if (resp.IsSuccessStatusCode)
            {
                Store(ParseTokens(await resp.Content.ReadAsStringAsync()));
                _db.Log("INFO", "Token POL ADV renovado com sucesso.");
                return true;
            }
        }
        catch (Exception e) { _db.Log("WARNING", $"Falha ao renovar token POL: {e.Message}"); }
        return false;
    }

    HttpRequestMessage Get(string path, bool tenant = true)
    {
        var req = new HttpRequestMessage(HttpMethod.Get, GatewayUrl + path);
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _accessToken);
        req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        if (tenant)
        {
            req.Headers.Add("x-officeid", config.PolOfficeId);
            req.Headers.Add("x-selecteduserid", _apiSub);
        }
        return req;
    }

    /// <summary>Primeiro login do usuário: usa o primeiro escritório ao qual ele pertence.</summary>
    async Task LoadOffice()
    {
        try
        {
            var resp = await Http.SendAsync(Get("/services/lawoffice/api/office/offices", tenant: false));
            if (resp.IsSuccessStatusCode && JsonNode.Parse(await resp.Content.ReadAsStringAsync()) is JsonArray { Count: > 0 } offices)
            {
                config.PolOfficeId = offices[0]!["id"]!.ToString();
                _db.Log("INFO", $"Escritório POL detectado: {offices[0]!["name"]} (#{config.PolOfficeId})");
            }
        }
        catch (Exception e) { _db.Log("WARNING", $"Não foi possível detectar o escritório do usuário: {e.Message}"); }
    }

    /// <summary>Busca todas as páginas (o Python parava nas 100 primeiras).</summary>
    async Task<List<JsonObject>?> FetchAll(string path, string label)
    {
        const int size = 100;
        var all = new List<JsonObject>();
        for (int page = 0; page < 50; page++)
        {
            var sep = path.Contains('?') ? '&' : '?';
            var resp = await Http.SendAsync(Get($"{path}{sep}size={size}&page={page}"));
            var body = await resp.Content.ReadAsStringAsync();
            if (!resp.IsSuccessStatusCode)
            {
                _db.Log("ERROR", $"Erro ao buscar {label} ({(int)resp.StatusCode}): {body}");
                return null;
            }
            var arr = (JsonNode.Parse(body) as JsonArray ?? []).OfType<JsonObject>().ToList();
            // Guarda contra um gateway que ignore "page" e devolva sempre a mesma página
            if (arr.Count > 0 && all.Any(o => o["id"]?.ToString() == arr[0]["id"]?.ToString())) break;
            all.AddRange(arr);
            if (arr.Count < size) break;
        }
        return all;
    }

    public async Task<List<JsonObject>> GetTasks()
    {
        if (!await Authenticate()) return [];
        try
        {
            var tasks = await FetchAll("/services/lawoffice/api/task/tasks?active=true&sort=deadline,asc", "tarefas");
            if (tasks == null) return [];
            foreach (var t in tasks)
                _db.UpsertCachedItem($"task_{t["id"]}", "TASK", (string?)t["name"] ?? "Tarefa sem título",
                    (string?)t["description"] ?? "", (string?)t["status"] ?? "PENDING",
                    t["priority"]?.ToString() ?? "MEDIUM", (string?)t["deadline"], t.ToJsonString());
            _db.Log("INFO", $"{tasks.Count} tarefas sincronizadas do POL ADV.");
            return tasks;
        }
        catch (Exception e)
        {
            _db.Log("ERROR", $"Exceção ao consultar tarefas: {e.Message}");
            return [];
        }
    }

    public async Task<List<JsonObject>> GetEvents()
    {
        if (!await Authenticate()) return [];
        try
        {
            var events = await FetchAll("/services/lawoffice/api/calendar/events?sort=startDate,asc", "eventos");
            if (events == null) return [];
            foreach (var ev in events)
                _db.UpsertCachedItem($"event_{ev["id"]}", "EVENT", (string?)ev["title"] ?? "Evento sem título",
                    (string?)ev["description"] ?? "", "SCHEDULED", "NORMAL", (string?)ev["startDate"], ev.ToJsonString());
            _db.Log("INFO", $"{events.Count} eventos de calendário sincronizados do POL ADV.");
            return events;
        }
        catch (Exception e)
        {
            _db.Log("ERROR", $"Exceção ao consultar eventos: {e.Message}");
            return [];
        }
    }
}
