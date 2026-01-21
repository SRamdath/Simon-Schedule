# streamlit_app.py
# Weekly schedule (calendar-ish UI) + Content library + Export to PDF
# Requirements: streamlit, reportlab

import io
import html
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta

import streamlit as st
import streamlit.components.v1 as components

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch


# =========================
# Page config
# =========================
st.set_page_config(page_title="Weekly Schedule", layout="wide")


# =========================
# Models
# =========================
@dataclass
class Event:
    name: str
    start: datetime
    end: datetime
    color: str
    kind: str  # "teaching" or "task"


# =========================
# Time helpers
# =========================
def start_of_week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())

def minutes_since_midnight(t: time) -> int:
    return t.hour * 60 + t.minute

def fmt_ampm(t: time) -> str:
    # Windows-safe AM/PM formatting
    dt = datetime.combine(date.today(), t)
    s = dt.strftime("%I:%M %p")
    return s.lstrip("0")

def fmt_ampm_dt(dt: datetime) -> str:
    return fmt_ampm(dt.time())

def snap_15(dt: datetime) -> datetime:
    return dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)


# =========================
# Fixed teaching blocks
# =========================
# weekday: Monday=0 ... Sunday=6
TEACHING = {
    0: [("Beginner Class", time(17, 0), time(18, 0), "#a855f7"),
        ("Leo Mootoo",     time(18, 15), time(19, 15), "#ef4444")],
    1: [("Pranava Classes", time(13, 30), time(17, 30), "#14b8a6"),
        ("Advanced Class",  time(18, 30), time(19, 30), "#3b82f6")],
    2: [("Jaydon", time(18, 30), time(19, 30), "#f97316")],
    3: [("Beginner Class", time(17, 0), time(18, 0), "#a855f7")],
    4: [("Leo Mootoo", time(17, 0), time(18, 0), "#ef4444")],
    6: [("Amrit", time(19, 0), time(20, 0), "#84cc16")],
}


# =========================
# Task specs
# =========================
# - My System: 2x 1h blocks (override pins one on Monday 1–2PM)
# - Calculation: 4x 1h blocks mixed across the week
# - Opening vs Endgame: forced onto different days
# - chessable/website tasks: only AFTER 9 PM (21:00)
# - Gym: must end by 9 PM AND gym days must be >= 2 days apart
TASK_SPECS = [
    ("My System", 1.0, 2, "#8b5cf6"),
    ("Calculation", 1.0, 4, "#f472b6"),
    ("Opening Study", 1.0, 5, "#6366f1"),
    ("Middlegame Study", 1.5, 2, "#ec4899"),
    ("Endgame Study", 1.0, 2, "#22c55e"),
    ("chessable/website tasks", 2.0, 2, "#0ea5e9"),
    ("League Study", 2.0, 2, "#f59e0b"),
    ("Gym", 0.75, 2, "#10b981"),
]

# Scheduling rules
BUFFER_MIN = 15
WINDOW_START = time(13, 0)         # place tasks only 1 PM..
WINDOW_END_NEXTDAY = time(2, 0)    # ..to 2 AM next day
MAX_TASKS_WEEKDAY = 3
MAX_TASKS_WEEKEND = 4


# =========================
# Content library (easy to edit)
# =========================
CONTENT_LIBRARY = {
    "Openings": [
        "Add opening resources here",
    ],
    "Middlegames": [
        "Add middlegame resources here",
    ],
    "Endgames": [
        "Add endgame resources here",
    ],
    "Calculation": [
        "Addcalculation resources here (Shanky, sheets, books)",
    ],
    "Special topics": [
        "Add special topics here (prophylaxis, rook + knight)",
    ],
    "Student material": [
        "Jaydon lesson notes",
    
    ],
    "Workout content": [
        "Routine A (upper)",
        "Routine B (lower)",
    ],
    "League content": [
        "Wave control notes",
        "VOD review checklist",
    ],
}


# =========================
# Scheduling engine
# =========================
def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return not (a_end <= b_start or a_start >= b_end)

def can_place(events: list[Event], start: datetime, end: datetime) -> bool:
    # enforce buffer both sides
    s = start - timedelta(minutes=BUFFER_MIN)
    e = end + timedelta(minutes=BUFFER_MIN)
    for ev in events:
        if overlaps(s, e, ev.start, ev.end):
            return False
    return True

def day_window(d: date):
    return (
        datetime.combine(d, WINDOW_START),
        datetime.combine(d + timedelta(days=1), WINDOW_END_NEXTDAY),
    )

def build_events(week_start: date) -> list[Event]:
    events: list[Event] = []

    # Teaching fixed
    for wd in range(7):
        d = week_start + timedelta(days=wd)
        for name, s, e, c in TEACHING.get(wd, []):
            events.append(Event(name, datetime.combine(d, s), datetime.combine(d, e), c, "teaching"))

    used_days = {name: set() for name, *_ in TASK_SPECS}
    tasks_per_day = {i: 0 for i in range(7)}
    gym_days: list[int] = []

    # Encourage using late time slots (10 PM–12 AM)
    preferred_start_times = [
        time(22, 0), time(22, 15), time(22, 30), time(22, 45),
        time(23, 0), time(23, 15), time(23, 30), time(23, 45),
        time(21, 0), time(21, 15), time(21, 30), time(21, 45),
        time(20, 0), time(20, 15), time(20, 30), time(20, 45),
        time(13, 0), time(13, 15), time(13, 30), time(13, 45),
        time(14, 0), time(14, 15), time(14, 30), time(14, 45),
        time(15, 0), time(15, 15), time(15, 30), time(15, 45),
        time(16, 0), time(16, 15), time(16, 30), time(16, 45),
        time(17, 0), time(17, 15), time(17, 30), time(17, 45),
        time(18, 0), time(18, 15), time(18, 30), time(18, 45),
        time(19, 0), time(19, 15), time(19, 30), time(19, 45),
    ]

    preferred_days = [0, 3, 2, 4, 5, 6, 1]  # Mon Thu Wed Fri Sat Sun Tue

    def try_place_task_on_day(task_name: str, dur_hours: float, color: str, wd: int) -> bool:
        nonlocal events

        d = week_start + timedelta(days=wd)
        wstart, wend = day_window(d)
        dur = timedelta(minutes=int(dur_hours * 60))

        candidates: list[datetime] = []
        for t in preferred_start_times:
            base_date = d if t >= WINDOW_START else (d + timedelta(days=1))
            start = snap_15(datetime.combine(base_date, t))
            if wstart <= start and start + dur <= wend:
                candidates.append(start)

        # full scan fallback
        cur = snap_15(wstart)
        while cur + dur <= wend:
            candidates.append(cur)
            cur += timedelta(minutes=15)

        # de-dupe
        seen = set()
        uniq = []
        for c in candidates:
            if c not in seen:
                uniq.append(c)
                seen.add(c)

        for start in uniq:
            end = start + dur

            # Gym rule: must end by 9 PM same day
            if task_name == "Gym":
                if start.date() != end.date():
                    continue
                if end.time() > time(21, 0):
                    continue

            # chessable/website tasks: only after 9 PM, start same day (not after midnight)
            if task_name == "chessable/website tasks":
                if start.time() < time(21, 0):
                    continue
                if start.date() != d:
                    continue

            if can_place(events, start, end):
                events.append(Event(task_name, start, end, color, "task"))
                return True

        return False

    for name, hrs, count, color in TASK_SPECS:
        for _ in range(count):
            placed = False

            for wd in preferred_days:
                if wd in used_days[name]:
                    continue

                # Opening vs Endgame must be on different days
                if name == "Opening Study" and wd in used_days.get("Endgame Study", set()):
                    continue
                if name == "Endgame Study" and wd in used_days.get("Opening Study", set()):
                    continue

                limit = MAX_TASKS_WEEKEND if wd >= 5 else MAX_TASKS_WEEKDAY
                if tasks_per_day[wd] >= limit:
                    continue

                if name == "Gym":
                    if any(abs(wd - gd) < 2 for gd in gym_days):
                        continue

                if try_place_task_on_day(name, hrs, color, wd):
                    used_days[name].add(wd)
                    tasks_per_day[wd] += 1
                    if name == "Gym":
                        gym_days.append(wd)
                    placed = True
                    break

            if not placed:
                for wd in preferred_days:
                    if wd in used_days[name]:
                        continue

                    if name == "Opening Study" and wd in used_days.get("Endgame Study", set()):
                        continue
                    if name == "Endgame Study" and wd in used_days.get("Opening Study", set()):
                        continue

                    if name == "Gym" and any(abs(wd - gd) < 2 for gd in gym_days):
                        continue

                    if try_place_task_on_day(name, hrs, color, wd):
                        used_days[name].add(wd)
                        if name == "Gym":
                            gym_days.append(wd)
                        placed = True
                        break

    events.sort(key=lambda e: e.start)
    return events


# =========================
# Calendar render (fixed columns + scroll-to-1PM)
# =========================
def render_week_calendar(events: list[Event], week_start: date) -> str:
    slot_min = 15
    px_per_slot = 16
    header_h = 52
    time_col_w = 84
    day_col_min_w = 170

    total_slots = (24 * 60) // slot_min
    grid_h = total_slots * px_per_slot + 90

    per_day_blocks = {i: [] for i in range(7)}

    for e in events:
        day_idx = (e.start.date() - week_start).days
        if not (0 <= day_idx <= 6):
            continue

        st_min = minutes_since_midnight(e.start.time())
        en_min = minutes_since_midnight(e.end.time())
        if e.end.date() != e.start.date():
            en_min = 24 * 60

        if en_min <= st_min:
            continue

        top_px = header_h + (st_min / slot_min) * px_per_slot
        height_px = max(14, ((en_min - st_min) / slot_min) * px_per_slot)

        title = html.escape(e.name)
        tlabel = html.escape(f"{fmt_ampm_dt(e.start)} – {fmt_ampm_dt(e.end)}")

        per_day_blocks[day_idx].append(f"""
          <div class="event" style="top:{top_px}px;height:{height_px}px;background:{e.color};">
            <div class="event-title">{title}</div>
            <div class="event-time">{tlabel}</div>
          </div>
        """)

    lines_html = []
    time_labels_html = []
    for slot in range(total_slots + 1):
        minute = slot * slot_min
        top = header_h + slot * px_per_slot
        is_hour = (minute % 60 == 0)
        lines_html.append(
            f"<div class='gridline' style='top:{top}px;opacity:{0.35 if is_hour else 0.15};'></div>"
        )
        if is_hour:
            hour = (minute // 60) % 24
            time_labels_html.append(
                f"<div class='time-label' style='top:{top}px;'>{html.escape(fmt_ampm(time(hour, 0)))}</div>"
            )

    days = [week_start + timedelta(days=i) for i in range(7)]
    day_labels = [d.strftime("%a") for d in days]
    day_dates = [d.strftime("%b %d") for d in days]

    style = f"""
    <style>
      :root {{
        --bg: #0c1528;
        --text: rgba(255,255,255,0.93);
        --muted: rgba(255,255,255,0.65);
      }}
      .cal-wrap {{
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 16px;
        overflow: hidden;
        background: var(--bg);
      }}
      .cal-header {{
        display: grid;
        grid-template-columns: {time_col_w}px repeat(7, minmax({day_col_min_w}px, 1fr));
        height: {header_h}px;
        background: rgba(255,255,255,0.05);
        border-bottom: 1px solid rgba(255,255,255,0.10);
        align-items: center;
      }}
      .timehead {{
        padding-left: 10px;
        font-size: 12px;
        color: var(--muted);
      }}
      .dayhead {{
        text-align: center;
        line-height: 1.05;
        color: var(--text);
      }}
      .dayname {{
        font-size: 13px;
        font-weight: 850;
        letter-spacing: 0.2px;
      }}
      .daydate {{
        font-size: 11px;
        color: var(--muted);
        margin-top: 3px;
      }}
      .cal-body {{
        display: grid;
        grid-template-columns: {time_col_w}px repeat(7, minmax({day_col_min_w}px, 1fr));
        position: relative;
        height: 760px;
        overflow-y: auto;
      }}
      .inner-height {{
        position: relative;
        height: {header_h + grid_h}px;
      }}
      .timecol {{
        position: relative;
        background: rgba(255,255,255,0.02);
        border-right: 1px solid rgba(255,255,255,0.10);
      }}
      .time-label {{
        position: absolute;
        left: 10px;
        transform: translateY(-50%);
        font-size: 11px;
        color: var(--muted);
        white-space: nowrap;
      }}
      .daycol {{
        position: relative;
        border-right: 1px solid rgba(255,255,255,0.06);
        background: rgba(255,255,255,0.01);
      }}
      .daycol:last-child {{ border-right: none; }}
      .gridline {{
        position: absolute;
        left: 0;
        right: 0;
        height: 1px;
        background: rgba(255,255,255,0.12);
      }}
      .event-layer {{
        position: absolute;
        top: 0;
        left: {time_col_w}px;
        right: 0;
        height: {header_h + grid_h}px;
        display: grid;
        grid-template-columns: repeat(7, 1fr);
        pointer-events: none;
      }}
      .event-col {{
        position: relative;
        height: {header_h + grid_h}px;
      }}
      .event {{
        position: absolute;
        left: 10px;
        right: 10px;
        border-radius: 16px;
        padding: 12px 12px;
        box-shadow: 0 12px 28px rgba(0,0,0,0.40);
        overflow: hidden;
        border: 1px solid rgba(255,255,255,0.18);
        color: #ffffff;
        text-shadow: 0 1px 2px rgba(0,0,0,0.70);
        background-image: linear-gradient(
          to bottom,
          rgba(0,0,0,0.14),
          rgba(0,0,0,0.33)
        );
        background-blend-mode: multiply;
      }}
      .event-title {{
        font-size: 13px;
        font-weight: 850;
        line-height: 1.1;
        margin-bottom: 4px;
      }}
      .event-time {{
        font-size: 11px;
        font-weight: 650;
        opacity: 0.95;
      }}
    </style>
    """

    header_cells = ["<div class='timehead'>Time</div>"]
    for i in range(7):
        header_cells.append(
            f"<div class='dayhead'><div class='dayname'>{html.escape(day_labels[i])}</div>"
            f"<div class='daydate'>{html.escape(day_dates[i])}</div></div>"
        )

    day_cols_html = ["<div class='daycol'></div>" for _ in range(7)]
    event_cols_html = []
    for i in range(7):
        inner = "\n".join(per_day_blocks[i])
        event_cols_html.append(f"<div class='event-col'>{inner}</div>")

    scroll_js = f"""
    <script>
      setTimeout(() => {{
        const scroller = document.querySelector('.cal-body');
        if (!scroller) return;
        const headerH = {header_h};
        const pxPerSlot = {px_per_slot};
        const slotsPerHour = 60 / {slot_min};
        const pxPerHour = pxPerSlot * slotsPerHour;
        scroller.scrollTop = headerH + (13 * pxPerHour) - 40;
      }}, 50);
    </script>
    """

    return f"""
    {style}
    <div class="cal-wrap">
      <div class="cal-header">
        {''.join(header_cells)}
      </div>

      <div class="cal-body">
        <div class="inner-height" style="grid-column: 1 / span 8;">
          <div class="timecol" style="position:absolute;left:0;top:0;width:{time_col_w}px;height:{header_h + grid_h}px;">
            {''.join(lines_html)}
            {''.join(time_labels_html)}
          </div>

          <div style="position:absolute;left:{time_col_w}px;right:0;top:0;height:{header_h + grid_h}px;
                      display:grid;grid-template-columns:repeat(7, 1fr);">
            {''.join(day_cols_html)}
          </div>

          <div class="event-layer">
            {''.join(event_cols_html)}
          </div>
        </div>
      </div>
    </div>
    {scroll_js}
    """


# =========================
# PDF Export
# =========================
def build_pdf_bytes(week_start: date, events: list[Event], content_library: dict) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter

    left = 0.75 * inch
    y = height - 0.75 * inch
    line_h = 12

    def new_page():
        nonlocal y
        c.showPage()
        y = height - 0.75 * inch

    def draw_line(text: str, font="Helvetica", size=10, indent=0):
        nonlocal y
        if y < 0.75 * inch:
            new_page()
        c.setFont(font, size)
        c.drawString(left + indent, y, text)
        y -= line_h

    def wrap_text(text: str, max_chars: int):
        words = text.split()
        lines = []
        cur = ""
        for w in words:
            if len(cur) + len(w) + (1 if cur else 0) <= max_chars:
                cur = (cur + " " + w).strip()
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    week_end = week_start + timedelta(days=6)
    title = f"Weekly Schedule: {week_start.strftime('%b %d, %Y')} (Mon) → {week_end.strftime('%b %d, %Y')} (Sun)"
    draw_line(title, font="Helvetica-Bold", size=14)
    y -= 6

    events_by_day = {i: [] for i in range(7)}
    for e in events:
        idx = (e.start.date() - week_start).days
        if 0 <= idx <= 6:
            events_by_day[idx].append(e)
    for i in range(7):
        events_by_day[i].sort(key=lambda ev: ev.start)

    draw_line("Schedule", font="Helvetica-Bold", size=12)
    y -= 2

    for i in range(7):
        d = week_start + timedelta(days=i)
        draw_line(f"{d.strftime('%A')} ({d.strftime('%b %d')})", font="Helvetica-Bold", size=11)
        if not events_by_day[i]:
            draw_line("• (no events)", indent=10)
        else:
            for e in events_by_day[i]:
                item = f"• {fmt_ampm_dt(e.start)} – {fmt_ampm_dt(e.end)}  |  {e.name} [{e.kind}]"
                for ln in wrap_text(item, max_chars=95):
                    draw_line(ln, indent=10)
        y -= 4

    draw_line("Content Library", font="Helvetica-Bold", size=12)
    y -= 2
    for cat, items in content_library.items():
        draw_line(cat, font="Helvetica-Bold", size=11)
        if not items:
            draw_line("• (empty)", indent=10)
        else:
            for it in items:
                for ln in wrap_text(f"• {it}", max_chars=95):
                    draw_line(ln, indent=10)
        y -= 4

    c.save()
    return buf.getvalue()


# =========================
# UI
# =========================
st.title("🗓️ Weekly Schedule")

week_start = start_of_week_monday(date.today())
events = build_events(week_start)

# --- MANUAL OVERRIDE (keep this) ---
# Pin Monday "My System" to 1:00–2:00 PM
mon = week_start  # Monday date
new_start = datetime.combine(mon, time(13, 0))  # 1:00 PM
new_end   = datetime.combine(mon, time(14, 0))  # 2:00 PM

for ev in events:
    if ev.name == "My System" and (ev.start.date() == mon):
        ev.start = new_start
        ev.end = new_end
        break

events.sort(key=lambda e: e.start)

cal_html = render_week_calendar(events, week_start)
components.html(cal_html, height=860, scrolling=False)

pdf_bytes = build_pdf_bytes(week_start, events, CONTENT_LIBRARY)
st.download_button(
    label="⬇️ Export this week to PDF",
    data=pdf_bytes,
    file_name=f"weekly_schedule_{week_start.isoformat()}.pdf",
    mime="application/pdf",
)

st.markdown("---")
st.markdown("## 📚 Content")
st.caption("Needs to fill.")

for category, items in CONTENT_LIBRARY.items():
    with st.expander(category):
        if not items:
            st.write("— (empty)")
        else:
            for item in items:
                st.write("•", item)









