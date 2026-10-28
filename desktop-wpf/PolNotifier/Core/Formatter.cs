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
        // Cada seção em ordem de prazo (sem prazo por último)
        foreach (var list in new[] { c.Today, c.Tomorrow, c.ThisWeek, c.Overdue })
            list.Sort((a, b) => (a.Deadline ?? DateTime.MaxValue).CompareTo(b.Deadline ?? DateTime.MaxValue));
        return c;
    }

    static string H(string s) => WebUtility.HtmlEncode(s);

    /// <summary>Gradiente de urgência: vermelho (vence hoje) → laranja → amarelo → verde (7+ dias).
    /// Atrasado = vermelho mais escuro; sem prazo = cinza. `dark` = tom claro para fundo escuro
    /// (widget/painel); senão tom escuro com contraste ≥ 4.5:1 sobre branco (e-mail).</summary>
    public static string UrgencyColor(double? daysLeft, bool dark = false)
    {
        if (daysLeft is not { } d) return dark ? "#a1a1aa" : "#57534e";
        if (d < 0) return dark ? "#f87171" : "#991b1b";
        var hue = Math.Clamp(d, 0, 7) / 7 * 135;
        if (dark) return Hsl(hue, 0.80, 0.66);
        // Amarelo/laranja são claros por natureza: escurece até 4.6:1 sobre branco
        var l = 0.42;
        while (Contrast(Hsl(hue, 0.85, l)) < 4.6) l -= 0.01;
        return Hsl(hue, 0.85, l);
    }

    static double Contrast(string hex)
    {
        double Ch(int i)
        {
            var v = Convert.ToInt32(hex.Substring(i, 2), 16) / 255.0;
            return v <= 0.03928 ? v / 12.92 : Math.Pow((v + 0.055) / 1.055, 2.4);
        }
        var lum = 0.2126 * Ch(1) + 0.7152 * Ch(3) + 0.0722 * Ch(5);
        return 1.05 / (lum + 0.05);
    }

    public static string UrgencyColor(Item it, bool dark = false) =>
        UrgencyColor(it.Deadline is { } d ? (d.Date - DateTime.Today).TotalDays : null, dark);

    static string Hsl(double h, double s, double l)
    {
        double c = (1 - Math.Abs(2 * l - 1)) * s, x = c * (1 - Math.Abs(h / 60 % 2 - 1)), m = l - c / 2;
        var (r, g, b) = h < 60 ? (c, x, 0d) : h < 120 ? (x, c, 0d) : (0d, c, x);
        return $"#{(int)Math.Round((r + m) * 255):x2}{(int)Math.Round((g + m) * 255):x2}{(int)Math.Round((b + m) * 255):x2}";
    }

    /// <summary>Urgente = há prazo vencendo hoje; senão é o resumo diário.</summary>
    public static bool IsUrgent(ClassifiedItems c) => c.Today.Count > 0;

    /// <summary>Título do e-mail (também usado no corpo).</summary>
    public static string EmailTitle() => $"Publicações Online - Relatório de Tarefas Diário para {DateTime.Today.ToString("dd/MM/yyyy", Br)}";

    /// <summary>Assunto = título; com prazo vencendo hoje ganha o prefixo URGENTE.</summary>
    public static string EmailSubject(ClassifiedItems c) => (IsUrgent(c) ? "URGENTE: " : "") + EmailTitle();

    static string Plural(int n, string one, string many) => $"{n} {(n == 1 ? one : many)}";

    static string Snippet(string s, int max = 170)
    {
        s = System.Text.RegularExpressions.Regex.Replace(s, @"\s+", " ").Trim();
        return s.Length > max ? s[..max].TrimEnd() + "…" : s;
    }

    // Paleta do e-mail: papel claro, tinta quase preta e uma cor por seção (texto escuro o bastante
    // para 4.5:1 sobre branco). Tabelas e estilos inline porque Gmail/Outlook ignoram <style> e flex.
    const string Ink = "#1c1917", Muted = "#57534e", Rule = "#e7e5e4", Paper = "#ffffff", Desk = "#f5f5f4";
    const string Serif = "Georgia, 'Times New Roman', serif";
    const string Sans = "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";

    /// <summary>E-mail do resumo diário / alerta urgente.</summary>
    public static string ToHtml(ClassifiedItems c, string userName = "")
    {
        var today = DateTime.Today;
        var urgent = IsUrgent(c);

        string Row(Item it)
        {
            var d = it.Deadline;
            var day = d?.ToString("dd/MM", Br) ?? "—";
            // Dia da semana para o ano corrente; de outro ano, o ano (atrasados antigos)
            var weekday = d is not { } w ? "sem prazo" : w.Year == today.Year ? w.ToString("ddd", Br).TrimEnd('.') : w.Year.ToString();
            var meta = it.IsTask ? "" : "Audiência / agenda";
            if (d is { } t && t.TimeOfDay < new TimeSpan(23, 59, 0) && t.TimeOfDay != TimeSpan.Zero)
                meta = (meta == "" ? "" : meta + " · ") + t.ToString("HH:mm", Br);
            var desc = it.Description != "" ? Snippet(it.Description) : "";
            var tone = UrgencyColor(it);
            var metaHtml = meta != "" ? $"<div style=\"font-size:12px;line-height:16px;color:{tone};padding-top:2px;\">{H(meta)}</div>" : "";
            var descHtml = desc != "" ? $"<div style=\"font-size:13px;line-height:19px;color:{Muted};padding-top:4px;\">{H(desc)}</div>" : "";
            return $"""
                <tr>
                  <td valign="top" width="64" style="padding:14px 0;border-top:1px solid {Rule};font-family:{Sans};">
                    <div style="font-size:15px;line-height:20px;font-weight:700;color:{tone};font-variant-numeric:tabular-nums;">{day}</div>
                    <div style="font-size:12px;line-height:16px;color:{Muted};">{H(weekday)}</div>
                  </td>
                  <td valign="top" style="padding:14px 0 14px 12px;border-top:1px solid {Rule};font-family:{Sans};word-break:break-word;overflow-wrap:anywhere;">
                    <div style="font-size:14px;line-height:20px;font-weight:600;color:{tone};">{H(it.Title.Trim())}</div>
                    {metaHtml}
                    {descHtml}
                  </td>
                </tr>
                """;
        }

        string Section(string title, string color, List<Item> items, int limit = int.MaxValue, string? empty = null)
        {
            if (items.Count == 0 && empty == null) return "";
            var rows = items.Count == 0
                ? $"<tr><td colspan=\"2\" style=\"padding:14px 0;border-top:1px solid {Rule};font-family:{Sans};font-size:14px;line-height:20px;color:{Muted};\">{empty}</td></tr>"
                : string.Concat(items.Take(limit).Select(Row));
            var more = items.Count > limit
                ? $"<tr><td colspan=\"2\" style=\"padding:12px 0 0;border-top:1px solid {Rule};font-family:{Sans};font-size:13px;color:{Muted};\">E mais {items.Count - limit} no <a href=\"https://office.pol.adv.br/\" style=\"color:{Ink};\">POL ADV</a>.</td></tr>"
                : "";
            return $"""
                <tr><td style="padding:28px 32px 0;">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                      <td style="padding-bottom:6px;font-family:{Sans};font-size:13px;line-height:18px;font-weight:700;color:{color};white-space:nowrap;">{title}</td>
                      <td align="right" style="padding-bottom:6px;font-family:{Sans};font-size:13px;line-height:18px;color:{Muted};font-variant-numeric:tabular-nums;">{items.Count}</td>
                    </tr>
                  </table>
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                    {rows}{more}
                  </table>
                </td></tr>
                """;
        }

        var title = EmailTitle();
        var name = string.IsNullOrWhiteSpace(userName) ? "Olá" : H(userName);
        var intro = $"{name}, estas são suas tarefas e agenda para os próximos dias.";
        // Pré-visualização da caixa de entrada (oculta no corpo)
        var preview = c.Today.Count > 0 ? $"{Plural(c.Today.Count, "item vence", "itens vencem")} hoje." : "Nenhuma tarefa/agendamento para hoje.";
        var headColor = urgent ? "#991b1b" : Ink;
        var band = urgent
            ? $"<tr><td style=\"background:#b91c1c;padding:10px 32px;font-family:{Sans};font-size:12px;line-height:16px;font-weight:700;letter-spacing:0.08em;color:#ffffff;text-transform:uppercase;\">Atenção imediata</td></tr>"
            : "";
        var sent = DateTime.Now.ToString("dd/MM/yyyy 'às' HH:mm", Br);

        return $"""
            <!DOCTYPE html>
            <html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>{H(title)}</title></head>
            <body style="margin:0;padding:0;background:{Desk};">
              <div style="display:none;max-height:0;overflow:hidden;color:{Desk};">{H(preview)}</div>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{Desk};">
                <tr><td align="center" style="padding:24px 12px;">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;background:{Paper};border-radius:12px;overflow:hidden;">
                    {band}
                    <tr><td style="padding:28px 32px 0;font-family:{Serif};font-size:24px;line-height:31px;font-weight:700;letter-spacing:-0.01em;color:{headColor};">{H(title)}</td></tr>
                    <tr><td style="padding:12px 32px 0;font-family:{Sans};font-size:15px;line-height:22px;color:{Ink};">{intro}</td></tr>
                    {Section("Hoje", UrgencyColor(0), c.Today, empty: "Nenhuma tarefa/agendamento para hoje.")}
                    {Section("Próximos 2 dias", UrgencyColor(1.5), c.Tomorrow)}
                    {Section("Ainda nesta semana", UrgencyColor(5), c.ThisWeek)}
                    {Section("Atrasados e sem prazo", UrgencyColor(-1), c.Overdue, limit: 10)}
                    <tr><td style="padding:32px 32px 0;">
                      <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
                        <td style="background:{Ink};border-radius:8px;">
                          <a href="https://office.pol.adv.br/" style="display:inline-block;padding:12px 20px;font-family:{Sans};font-size:14px;line-height:18px;font-weight:600;color:#ffffff;text-decoration:none;">Abrir o POL ADV</a>
                        </td>
                      </tr></table>
                    </td></tr>
                    <tr><td style="padding:28px 32px;font-family:{Sans};font-size:12px;line-height:18px;color:{Muted};">
                      Enviado pelo POL ADV Notifier em {sent}. Você recebe este resumo porque ativou as notificações por e-mail no app.
                    </td></tr>
                  </table>
                </td></tr>
              </table>
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
