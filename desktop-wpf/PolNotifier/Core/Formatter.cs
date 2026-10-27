using System.Globalization;
using System.Net;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace PolNotifier.Core;

public sealed record Item(string Id, string Type, string Title, string Description, string Priority,
                          DateTime? Deadline, string DeadlineStr)
{
    public bool IsTask => Type == "Tarefa";
}

public sealed class ClassifiedItems
{
    public List<Item> Today { get; } = [];
    public List<Item> Tomorrow { get; } = [];
    public List<Item> ThisWeek { get; } = [];
    public List<Item> Overdue { get; } = [];
    public IEnumerable<Item> All => Today.Concat(Tomorrow).Concat(ThisWeek).Concat(Overdue);
    public IEnumerable<Item> Upcoming => Today.Concat(Tomorrow).Concat(ThisWeek);
    public int TotalCount => Today.Count + Tomorrow.Count + ThisWeek.Count + Overdue.Count;
    public bool HasItems => TotalCount > 0;
}

/// <summary>Classificação e mensagens, como app/notifier/formatter.py.</summary>
public static class Formatter
{
    static readonly CultureInfo Br = CultureInfo.GetCultureInfo("pt-BR");

    /// <summary>Datas da API vêm em UTC ("...T02:59:59Z" = 23:59 em Brasília); convertidas para o
    /// horário local antes de classificar (o Python classificava pela data UTC).</summary>
    public static DateTime? ParseDateTime(JsonNode? v)
    {
        if (v is not JsonValue jv) return null;
        if (jv.GetValueKind() == JsonValueKind.Number)
        {
            var n = jv.GetValue<double>();
            if (n > 10_000_000_000) n /= 1000; // timestamp em milissegundos
            return DateTimeOffset.FromUnixTimeMilliseconds((long)(n * 1000)).LocalDateTime;
        }
        if (jv.GetValueKind() == JsonValueKind.String &&
            DateTimeOffset.TryParse(jv.GetValue<string>(), CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out var dto))
            return dto.LocalDateTime;
        return null;
    }

    /// <summary>Prazos do POL são gravados como "fim do dia" (02:59:59Z = 23:59:59 em Brasília):
    /// nesses casos a hora não significa nada e só a data é exibida.</summary>
    static string Fmt(DateTime d) =>
        d.ToString(d.TimeOfDay >= new TimeSpan(23, 59, 0) || d.TimeOfDay == TimeSpan.Zero ? "dd/MM/yyyy" : "dd/MM/yyyy HH:mm", Br);
    static string S(JsonNode? n) => n is JsonValue v && v.GetValueKind() == JsonValueKind.String ? v.GetValue<string>() : n?.ToString() ?? "";

    public static ClassifiedItems Classify(IEnumerable<JsonObject> tasks, IEnumerable<JsonObject> events)
    {
        var c = new ClassifiedItems();
        var today = DateTime.Today;
        var next2 = today.AddDays(2);
        var endOfWeek = today.AddDays(7);

        void Place(Item it, DateTime d, bool overdueAllowed)
        {
            if (d < today) { if (overdueAllowed) c.Overdue.Add(it); }
            else if (d == today) c.Today.Add(it);
            else if (d <= next2) c.Tomorrow.Add(it);
            else if (d <= endOfWeek) c.ThisWeek.Add(it);
        }

        foreach (var t in tasks)
        {
            if (t["isArchived"]?.GetValueKind() == JsonValueKind.True) continue;
            if (S(t["status"]) is "FINISHED" or "CONCLUDED" or "DONE") continue;
            var dl = ParseDateTime(t["deadline"]);
            var it = new Item($"task_{t["id"]}", "Tarefa", t["name"] is null ? "Sem título" : S(t["name"]),
                              S(t["description"]), t["priority"] is null ? "Média" : S(t["priority"]),
                              dl, dl is { } d0 ? Fmt(d0) : "Sem prazo definido");
            // Tarefa sem prazo explícito entra em pendente
            if (dl is not { } d) c.Overdue.Add(it);
            else Place(it, d.Date, overdueAllowed: true);
        }

        foreach (var ev in events)
        {
            if (ParseDateTime(ev["startDate"]) is not { } start) continue;
            var title = ev["title"] is null ? "Sem título" : S(ev["title"]);
            var desc = S(ev["location"]);
            if (desc == "") desc = S(ev["description"]);
            var it = new Item($"event_{ev["id"]}", "Audiência / Agenda", title, desc,
                              title.Contains("audiência", StringComparison.OrdinalIgnoreCase) ? "Alta" : "Normal",
                              start, Fmt(start));
            Place(it, start.Date, overdueAllowed: false); // eventos passados não contam como atrasados
        }
        return c;
    }

    static string H(string s) => WebUtility.HtmlEncode(s);

    public static string ToHtml(ClassifiedItems c, string userName = "Prezado(a)")
    {
        string Section(string title, List<Item> items, string bg, string border, string badge)
        {
            if (items.Count == 0) return "";
            var sb = new StringBuilder($"""
                <div style="margin-bottom: 24px; border: 1px solid {border}; border-radius: 8px; overflow: hidden; background: #ffffff;">
                    <div style="background: {bg}; color: #ffffff; padding: 10px 16px; font-weight: bold; font-size: 15px;">
                        <span>{title}</span>
                        <span style="background: rgba(255,255,255,0.25); padding: 2px 8px; border-radius: 12px; font-size: 12px;">{items.Count} {badge}</span>
                    </div>
                    <div style="padding: 12px 16px;">
                """);
            foreach (var it in items)
            {
                var desc = it.Description != "" ? $"<div style='font-size: 12px; color: #6b7280; margin-top: 4px;'>{H(it.Description)}</div>" : "";
                sb.Append($"""
                    <div style="padding: 10px 0; border-bottom: 1px solid #f3f4f6;">
                        <span style="font-weight: 600; color: #111827; font-size: 14px;">[{H(it.Type)}] {H(it.Title)}</span>
                        <span style="font-size: 12px; color: #4b5563; white-space: nowrap; font-weight: bold;"> 📅 {it.DeadlineStr}</span>
                        {desc}
                    </div>
                    """);
            }
            sb.Append("</div></div>");
            return sb.ToString();
        }

        return $"""
            <!DOCTYPE html>
            <html><head><meta charset="utf-8"></head>
            <body style="font-family: 'Segoe UI', Roboto, Arial, sans-serif; background: #f9fafb; margin: 0; padding: 20px;">
              <div style="max-width: 680px; margin: 0 auto; background: #ffffff; border-radius: 10px; border: 1px solid #e5e7eb; padding: 24px;">
                <div style="border-bottom: 2px solid #3b82f6; padding-bottom: 12px; margin-bottom: 20px;">
                  <h1 style="margin: 0; font-size: 20px; color: #1e3a8a;">⚖️ POL ADV — Relatório de Prazos &amp; Compromissos</h1>
                  <p style="margin: 4px 0 0 0; font-size: 13px; color: #6b7280;">Olá, {H(userName)}! Segue o monitoramento atualizado em <strong>{DateTime.Today:dd/MM/yyyy}</strong>.</p>
                </div>
                {Section("🚨 VENCENDO HOJE", c.Today, "#dc2626", "#fca5a5", "urgente(s)")}
                {Section("⚠️ VENCENDO AMANHÃ / PRÓXIMOS 2 DIAS", c.Tomorrow, "#d97706", "#fcd34d", "item(ns)")}
                {Section("📅 VENCENDO ESTA SEMANA", c.ThisWeek, "#2563eb", "#bfdbfe", "item(ns)")}
                {Section("⏳ TAREFAS ATRASADAS / PENDENTES", c.Overdue, "#4b5563", "#d1d5db", "atrasada(s)")}
                <div style="text-align: center; margin-top: 30px; font-size: 11px; color: #9ca3af; border-top: 1px solid #e5e7eb; padding-top: 12px;">
                  Notificação automática gerada pelo <strong>POL ADV Desktop Notifier</strong>.<br>
                  Para gerenciar seus prazos, acesse <a href="https://office.pol.adv.br/" style="color: #2563eb;">office.pol.adv.br</a>
                </div>
              </div>
            </body></html>
            """;
    }

    static string Lines(ClassifiedItems c, Func<string, string> header, Func<Item, string> line,
                        string overdueTitle, string moreText, string footer, string title)
    {
        var sb = new List<string> { title + "\n" };
        void Block(string h, IEnumerable<Item> items)
        {
            sb.Add(header(h));
            sb.AddRange(items.Select(line));
            sb.Add("");
        }
        if (c.Today.Count > 0) Block("🚨 VENCENDO HOJE:", c.Today);
        if (c.Tomorrow.Count > 0) Block("⚠️ AMANHÃ / PRÓXIMOS 2 DIAS:", c.Tomorrow);
        if (c.ThisWeek.Count > 0) Block("📅 ESTA SEMANA:", c.ThisWeek);
        if (c.Overdue.Count > 0)
        {
            sb.Add(header(overdueTitle));
            sb.AddRange(c.Overdue.Take(10).Select(line)); // limitar para não estourar a mensagem
            if (c.Overdue.Count > 10) sb.Add($"_...e mais {c.Overdue.Count - 10} {moreText}_");
            sb.Add("");
        }
        sb.Add(footer);
        return string.Join("\n", sb);
    }

    public static string ToTelegramMarkdown(ClassifiedItems c) => Lines(c, h => $"*{h}*",
        it => $"• *[{it.Type}]* {it.Title} — `{it.DeadlineStr}`", "⏳ ATRASADAS / PENDENTES:", "tarefas atrasadas",
        "🔗 [Acessar POL ADV](https://office.pol.adv.br)", "⚖️ *POL ADV — Resumo de Prazos*");

    public static string ToOpenWaText(ClassifiedItems c) => Lines(c, h => $"*{h}*",
        it => $"• *[{it.Type}]* {it.Title} ({it.DeadlineStr})", "⏳ TAREFAS ATRASADAS / PENDENTES:", "tarefas",
        "Acesse: https://office.pol.adv.br", "*⚖️ POL ADV — Resumo de Prazos*");
}
