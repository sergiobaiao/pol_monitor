using System.Text.Json;
using System.Text.Json.Nodes;

namespace PolNotifier.Core;

/// <summary>Cartão do Kanban / bloco do calendário, como o POL mostra (office.pol.adv.br/tasks e /calendar).</summary>
public sealed record BoardItem(string Id, bool IsTask, string Title, string Description, string Status,
                               string Priority, string Category, string CategoryColor, string Assignee,
                               DateTime? Deadline, DateTime? End, double Score = 0)
{
    public bool IsDone => Status == "DONE";
    public bool IsOverdue => !IsDone && Deadline is { } d && d < DateTime.Now;
    public bool NoDeadline => Deadline == null;

    /// <summary>Cor do bloco no calendário do POL: concluída = verde, atrasada = vermelho,
    /// pendente = azul; compromisso da agenda usa a cor da categoria.</summary>
    public string CalendarColor => IsTask ? (IsDone ? "#2f9e44" : IsOverdue ? "#9b1c1c" : "#1c64b5") : CategoryColor;

    /// <summary>Iniciais do responsável (primeiro + último nome), como o avatar do POL.</summary>
    public string Initials
    {
        get
        {
            var p = Assignee.Split(' ', StringSplitOptions.RemoveEmptyEntries);
            return p.Length == 0 ? "" : p.Length == 1 ? p[0][..1].ToUpperInvariant() : $"{char.ToUpperInvariant(p[0][0])}{char.ToUpperInvariant(p[^1][0])}";
        }
    }
}

public static class Board
{
    /// <summary>Colunas do Kanban do POL, na ordem.</summary>
    public static readonly (string Status, string Title)[] Columns =
        [("TO_DO", "A FAZER"), ("IN_PROGRESS", "EM ANDAMENTO"), ("REVIEW", "A REVISAR"), ("DONE", "CONCLUÍDAS")];

    public static readonly Dictionary<string, (string Label, string Arrow, string Color)> Priorities = new()
    {
        ["HIGH"] = ("Alta", "↑", "#a78bfa"), ["MEDIUM"] = ("Média", "—", "#facc15"), ["LOW"] = ("Baixa", "↓", "#60a5fa"),
    };

    static string S(JsonNode? n) => n is JsonValue v && v.GetValueKind() == JsonValueKind.String ? v.GetValue<string>() : n?.ToString() ?? "";

    public static List<BoardItem> Build(IEnumerable<JsonObject> tasks, IEnumerable<JsonObject> events)
    {
        var list = new List<BoardItem>();
        foreach (var t in tasks)
        {
            if (t["isArchived"]?.GetValueKind() == JsonValueKind.True) continue;
            list.Add(new BoardItem($"task_{t["id"]}", true, S(t["name"]).Trim(), S(t["description"]).Trim(),
                S(t["status"]) is { Length: > 0 } st ? st : "TO_DO", S(t["priority"]) is { Length: > 0 } p ? p : "MEDIUM",
                S(t["category"]?["name"]), S(t["category"]?["color"]) is { Length: > 0 } cc ? cc : "#71717a",
                S(t["assignedTo"]?["name"]), Formatter.ParseDateTime(t["deadline"]), null,
                t["score"] is JsonValue sv && sv.TryGetValue<double>(out var sc) ? sc : 0));
        }
        foreach (var e in events)
            list.Add(new BoardItem($"event_{e["id"]}", false, S(e["title"]).Trim(), S(e["description"]).Trim(), "EVENT", "MEDIUM",
                S(e["category"]?["name"]), S(e["category"]?["color"]) is { Length: > 0 } cc ? cc : "#1c64b5", "",
                Formatter.ParseDateTime(e["startDate"]), Formatter.ParseDateTime(e["endDate"])));
        return list;
    }
}
