from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional
import dateutil.parser

class ClassifiedItems:
    def __init__(self):
        self.today: List[Dict[str, Any]] = []
        self.tomorrow: List[Dict[str, Any]] = []
        self.this_week: List[Dict[str, Any]] = []
        self.overdue: List[Dict[str, Any]] = []

    @property
    def total_count(self) -> int:
        return len(self.today) + len(self.tomorrow) + len(self.this_week) + len(self.overdue)

    @property
    def has_items(self) -> bool:
        return self.total_count > 0

class NotificationFormatter:
    @staticmethod
    def parse_datetime(dt_val: Any) -> Optional[datetime]:
        if not dt_val:
            return None
        if isinstance(dt_val, (int, float)):
            # Se for timestamp em milissegundos
            if dt_val > 10000000000:
                dt_val = dt_val / 1000
            return datetime.fromtimestamp(dt_val)
        if isinstance(dt_val, str):
            try:
                return dateutil.parser.parse(dt_val)
            except Exception:
                return None
        return None

    @classmethod
    def classify_items(cls, tasks: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> ClassifiedItems:
        classified = ClassifiedItems()
        today = date.today()
        tomorrow = today + timedelta(days=1)
        next_2_days = today + timedelta(days=2)
        end_of_week = today + timedelta(days=7)

        # Processar tarefas
        for t in tasks:
            if t.get("isArchived") or t.get("status") in ["FINISHED", "CONCLUDED", "DONE"]:
                continue
            
            dl_dt = cls.parse_datetime(t.get("deadline"))
            item = {
                "id": f"task_{t.get('id')}",
                "type": "Tarefa",
                "title": t.get("name", "Sem título"),
                "description": t.get("description") or "",
                "priority": t.get("priority", "Média"),
                "deadline_dt": dl_dt,
                "deadline_str": dl_dt.strftime("%d/%m/%Y %H:%M") if dl_dt else "Sem prazo definido"
            }

            if not dl_dt:
                # Tarefa sem prazo explícito entra em pendente
                classified.overdue.append(item)
                continue

            item_date = dl_dt.date()
            if item_date < today:
                classified.overdue.append(item)
            elif item_date == today:
                classified.today.append(item)
            elif today < item_date <= next_2_days:
                classified.tomorrow.append(item)
            elif next_2_days < item_date <= end_of_week:
                classified.this_week.append(item)

        # Processar eventos
        for ev in events:
            start_dt = cls.parse_datetime(ev.get("startDate"))
            if not start_dt:
                continue

            item_date = start_dt.date()
            if item_date < today:
                continue # eventos passados não contam como atrasados

            item = {
                "id": f"event_{ev.get('id')}",
                "type": "Audiência / Agenda",
                "title": ev.get("title", "Sem título"),
                "description": ev.get("location") or ev.get("description") or "",
                "priority": "Alta" if "audiência" in ev.get("title", "").lower() else "Normal",
                "deadline_dt": start_dt,
                "deadline_str": start_dt.strftime("%d/%m/%Y %H:%M")
            }

            if item_date == today:
                classified.today.append(item)
            elif today < item_date <= next_2_days:
                classified.tomorrow.append(item)
            elif next_2_days < item_date <= end_of_week:
                classified.this_week.append(item)

        return classified

    @classmethod
    def to_html(cls, classified: ClassifiedItems, user_name: str = "Prezado(a)") -> str:
        data_extenso = date.today().strftime("%d/%m/%Y")
        
        def render_section(title: str, items: List[Dict[str, Any]], bg_color: str, border_color: str, badge_text: str):
            if not items:
                return ""
            html = f"""
            <div style="margin-bottom: 24px; border: 1px solid {border_color}; border-radius: 8px; overflow: hidden; background: #ffffff;">
                <div style="background: {bg_color}; color: #ffffff; padding: 10px 16px; font-weight: bold; font-size: 15px; display: flex; justify-content: space-between; align-items: center;">
                    <span>{title}</span>
                    <span style="background: rgba(255,255,255,0.25); padding: 2px 8px; border-radius: 12px; font-size: 12px;">{len(items)} {badge_text}</span>
                </div>
                <div style="padding: 12px 16px;">
            """
            for it in items:
                desc_html = f"<div style='font-size: 12px; color: #6b7280; margin-top: 4px;'>{it['description']}</div>" if it['description'] else ""
                html += f"""
                <div style="padding: 10px 0; border-bottom: 1px solid #f3f4f6;">
                    <div style="display: flex; justify-content: space-between; align-items: baseline;">
                        <span style="font-weight: 600; color: #111827; font-size: 14px;">[{it['type']}] {it['title']}</span>
                        <span style="font-size: 12px; color: #4b5563; white-space: nowrap; font-weight: bold;">📅 {it['deadline_str']}</span>
                    </div>
                    {desc_html}
                </div>
                """
            html += """
                </div>
            </div>
            """
            return html

        sec_today = render_section("🚨 VENCENDO HOJE", classified.today, "#dc2626", "#fca5a5", "urgente(s)")
        sec_tomorrow = render_section("⚠️ VENCENDO AMANHÃ / PRÓXIMOS 2 DIAS", classified.tomorrow, "#d97706", "#fcd34d", "item(ns)")
        sec_week = render_section("📅 VENCENDO ESTA SEMANA", classified.this_week, "#2563eb", "#bfdbfe", "item(ns)")
        sec_overdue = render_section("⏳ TAREFAS ATRASADAS / PENDENTES", classified.overdue, "#4b5563", "#d1d5db", "atrasada(s)")

        full_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background: #f9fafb; margin: 0; padding: 20px; }}
                .container {{ max-width: 680px; margin: 0 auto; background: #ffffff; border-radius: 10px; border: 1px solid #e5e7eb; padding: 24px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }}
                .header {{ border-bottom: 2px solid #3b82f6; padding-bottom: 12px; margin-bottom: 20px; }}
                .header h1 {{ margin: 0; font-size: 20px; color: #1e3a8a; }}
                .header p {{ margin: 4px 0 0 0; font-size: 13px; color: #6b7280; }}
                .footer {{ text-align: center; margin-top: 30px; font-size: 11px; color: #9ca3af; border-top: 1px solid #e5e7eb; padding-top: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>⚖️ POL ADV — Relatório de Prazos & Compromissos</h1>
                    <p>Olá, {user_name}! Segue o monitoramento atualizado em <strong>{data_extenso}</strong>.</p>
                </div>

                {sec_today}
                {sec_tomorrow}
                {sec_week}
                {sec_overdue}

                <div class="footer">
                    Notificação automática gerada pelo <strong>POL ADV Desktop Notifier</strong>.<br>
                    Para gerenciar seus prazos, acesse <a href="https://office.pol.adv.br/" style="color: #2563eb;">office.pol.adv.br</a>
                </div>
            </div>
        </body>
        </html>
        """
        return full_html

    @classmethod
    def to_telegram_markdown(cls, classified: ClassifiedItems) -> str:
        lines = ["⚖️ *POL ADV — Resumo de Prazos*\n"]
        
        if classified.today:
            lines.append("🚨 *VENCENDO HOJE:*")
            for it in classified.today:
                lines.append(f"• *[{it['type']}]* {it['title']} — `{it['deadline_str']}`")
            lines.append("")

        if classified.tomorrow:
            lines.append("⚠️ *AMANHÃ / PRÓXIMOS 2 DIAS:*")
            for it in classified.tomorrow:
                lines.append(f"• *[{it['type']}]* {it['title']} — `{it['deadline_str']}`")
            lines.append("")

        if classified.this_week:
            lines.append("📅 *ESTA SEMANA:*")
            for it in classified.this_week:
                lines.append(f"• *[{it['type']}]* {it['title']} — `{it['deadline_str']}`")
            lines.append("")

        if classified.overdue:
            lines.append("⏳ *ATRASADAS / PENDENTES:*")
            for it in classified.overdue[:10]: # limitar para não estourar mensagem
                lines.append(f"• *[{it['type']}]* {it['title']} — `{it['deadline_str']}`")
            if len(classified.overdue) > 10:
                lines.append(f"_...e mais {len(classified.overdue)-10} tarefas atrasadas_")
            lines.append("")

        lines.append("🔗 [Acessar POL ADV](https://office.pol.adv.br)")
        return "\n".join(lines)

    @classmethod
    def to_openwa_text(cls, classified: ClassifiedItems) -> str:
        lines = ["*⚖️ POL ADV — Resumo de Prazos*\n"]

        if classified.today:
            lines.append("*🚨 VENCENDO HOJE:*")
            for it in classified.today:
                lines.append(f"• *[{it['type']}]* {it['title']} ({it['deadline_str']})")
            lines.append("")

        if classified.tomorrow:
            lines.append("*⚠️ AMANHÃ / PRÓXIMOS 2 DIAS:*")
            for it in classified.tomorrow:
                lines.append(f"• *[{it['type']}]* {it['title']} ({it['deadline_str']})")
            lines.append("")

        if classified.this_week:
            lines.append("*📅 ESTA SEMANA:*")
            for it in classified.this_week:
                lines.append(f"• *[{it['type']}]* {it['title']} ({it['deadline_str']})")
            lines.append("")

        if classified.overdue:
            lines.append("*⏳ TAREFAS ATRASADAS / PENDENTES:*")
            for it in classified.overdue[:10]:
                lines.append(f"• *[{it['type']}]* {it['title']} ({it['deadline_str']})")
            if len(classified.overdue) > 10:
                lines.append(f"_...e mais {len(classified.overdue)-10} tarefas_")
            lines.append("")

        lines.append("Acesse: https://office.pol.adv.br")
        return "\n".join(lines)
