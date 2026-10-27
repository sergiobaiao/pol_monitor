using System.Net.Http;
using System.Net.Http.Json;
using MailKit.Net.Smtp;
using MailKit.Security;
using MimeKit;

namespace PolNotifier.Core;

/// <summary>Envio por E-mail, Telegram e WhatsApp (OpenWA), como app/notifier/*_service.py.
/// Cada envio devolve (sucesso, mensagem para o usuário/histórico).</summary>
public sealed class Notifiers(Config config)
{
    readonly Database _db = config.Db;
    static HttpClient Http => PolClient.Http;

    public async Task<(bool, string)> SendEmail(string subject, string html, List<string>? recipients = null)
    {
        var to = recipients ?? config.SmtpRecipients;
        if (to.Count == 0) return (false, "Nenhum destinatário de e-mail configurado.");
        var from = config.SmtpFrom is { Length: > 0 } f ? f : config.SmtpUser is { Length: > 0 } u ? u : "pol-notifier@local";
        try
        {
            var msg = new MimeMessage { Subject = subject, Body = new TextPart("html") { Text = html } };
            msg.From.Add(MailboxAddress.Parse(from));
            foreach (var r in to) msg.To.Add(MailboxAddress.Parse(r));
            using var smtp = new SmtpClient { Timeout = 15000 };
            var ssl = config.SmtpUseSsl || config.SmtpPort == 465 ? SecureSocketOptions.SslOnConnect : SecureSocketOptions.StartTls;
            await smtp.ConnectAsync(config.SmtpHost, config.SmtpPort, ssl);
            if (config.SmtpUser != "" && config.SmtpPass != "") await smtp.AuthenticateAsync(config.SmtpUser, config.SmtpPass);
            await smtp.SendAsync(msg);
            await smtp.DisconnectAsync(true);
            _db.Log("INFO", $"E-mail enviado com sucesso para {to.Count} destinatários: {string.Join(", ", to)}");
            return (true, "E-mail enviado com sucesso!");
        }
        catch (Exception e)
        {
            var err = $"Falha ao enviar e-mail: {e.Message}";
            _db.Log("ERROR", err);
            return (false, err);
        }
    }

    /// <summary>Envia para cada destinatário e consolida o resultado como os serviços Python.</summary>
    async Task<(bool, string)> SendEach(List<string> targets, Func<string, Task<HttpResponseMessage>> send,
                                        string label, string what, string okMsg, string partialMsg)
    {
        var errors = new List<string>();
        int ok = 0;
        foreach (var t in targets)
        {
            try
            {
                var resp = await send(t);
                if (resp.IsSuccessStatusCode) ok++;
                else errors.Add($"{what}{t}: HTTP {(int)resp.StatusCode} - {await resp.Content.ReadAsStringAsync()}");
            }
            catch (Exception e) { errors.Add($"{what}{t}: {e.Message}"); }
        }
        if (ok > 0)
        {
            _db.Log("INFO", $"{label}: mensagem enviada para {ok} destinatário(s).");
            return (true, errors.Count > 0 ? string.Format(partialMsg, ok, string.Join("; ", errors)) : string.Format(okMsg, ok));
        }
        var err = $"Falha no envio via {label}: {string.Join("; ", errors)}";
        _db.Log("ERROR", err);
        return (false, err);
    }

    public Task<(bool, string)> SendTelegram(string text, List<string>? chatIds = null)
    {
        var token = config.TelegramToken;
        var chats = chatIds ?? config.TelegramChatIds;
        if (token == "") return Task.FromResult((false, "Token do Telegram Bot não configurado."));
        if (chats.Count == 0) return Task.FromResult((false, "Nenhum Chat ID do Telegram configurado."));
        var url = $"https://api.telegram.org/bot{token}/sendMessage";
        return SendEach(chats, chat => Http.PostAsJsonAsync(url, new Dictionary<string, object>
            {
                ["chat_id"] = chat, ["text"] = text, ["parse_mode"] = "Markdown", ["disable_web_page_preview"] = true,
            }), "Telegram", "Chat ", "Mensagem enviada com sucesso para {0} chat(s)!",
            "Enviado para {0} chats, com falhas em: {1}");
    }

    public Task<(bool, string)> SendOpenWa(string text, List<string>? recipients = null)
    {
        var baseUrl = config.OpenWaUrl.TrimEnd('/');
        var to = recipients ?? config.OpenWaRecipients;
        if (baseUrl == "") return Task.FromResult((false, "URL do servidor OpenWA não configurada."));
        if (to.Count == 0) return Task.FromResult((false, "Nenhum número/destinatário do WhatsApp configurado."));
        var key = config.OpenWaApiKey;
        return SendEach(to, number =>
        {
            // Número sem sufixo vira chatId internacional
            var chatId = number.Contains('@') ? number : new string(number.Where(char.IsDigit).ToArray()) + "@c.us";
            var req = new HttpRequestMessage(HttpMethod.Post, $"{baseUrl}/sendText")
            {
                Content = JsonContent.Create(new Dictionary<string, string> { ["chatId"] = chatId, ["text"] = text }),
            };
            if (key != "")
            {
                req.Headers.Add("api_key", key);
                req.Headers.TryAddWithoutValidation("Authorization", $"Bearer {key}");
            }
            return Http.SendAsync(req);
        }, "OpenWA", "", "Mensagem enviada com sucesso para {0} destinatário(s) no WhatsApp!",
            "Enviado para {0} destinatários, erros: {1}");
    }
}
