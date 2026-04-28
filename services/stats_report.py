import calendar
import os
import csv
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.models import Exercise, ExerciseLog, WorkoutSession


@dataclass
class SessionSet:
    session_id: int
    at: datetime
    weight: float
    reps: Optional[int]
    set_no: int
    is_working: bool = False


def _fmt_num(value: Optional[float | int]) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _fmt_delta(value: Optional[float | int]) -> str:
    if value is None:
        return ""
    n = float(value)
    if n > 0:
        return f"+{_fmt_num(n)}"
    if n < 0:
        return f"-{_fmt_num(abs(n))}"
    return "0"


def _fmt_percent(value: Optional[float]) -> str:
    if value is None:
        return ""
    if abs(value) < 1e-9:
        return "0%"
    sign = "+" if value > 0 else "-"
    return f"{sign}{abs(value):.0f}%"


def _safe_growth(delta: float, start: float) -> Optional[float]:
    if abs(start) < 1e-9:
        return 0.0 if abs(delta) < 1e-9 else None
    return (delta / start) * 100


def _register_font() -> str:
    bundled = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "NotoSans-Regular.ttf"
    candidates = [
        str(bundled),
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        p = Path(path)
        if p.exists():
            font_name = "AppSans"
            try:
                pdfmetrics.registerFont(TTFont(font_name, str(p)))
            except Exception:
                continue
            return font_name
    return "Helvetica"


def _calc_avg(entries: list[SessionSet]) -> tuple[Optional[float], Optional[float]]:
    if not entries:
        return None, None
    avg_w = sum(x.weight for x in entries) / len(entries)
    rep_entries = [x.reps for x in entries if x.reps is not None]
    avg_r = (sum(rep_entries) / len(rep_entries)) if rep_entries else None
    return avg_w, avg_r


def _period_entries(entries: list[SessionSet], start: date, end: date) -> list[SessionSet]:
    return [e for e in entries if start <= e.at.date() <= end]


def _first_last(entries: list[SessionSet]) -> tuple[Optional[SessionSet], Optional[SessionSet]]:
    if not entries:
        return None, None
    ordered = sorted(entries, key=lambda x: (x.at, x.session_id, x.set_no))
    return ordered[0], ordered[-1]


def _build_block_table(
    title: str, headers: list[str], row: list[str], font_name: str, no_data_msg: Optional[str] = None
) -> list:
    styles = getSampleStyleSheet()
    styles["Normal"].fontName = font_name
    styles["Heading3"].fontName = font_name
    flow = [Paragraph(f"<b>{title}</b>", styles["Heading3"]), Spacer(1, 8)]
    data = [headers, row]
    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    flow.append(table)
    if no_data_msg:
        flow.append(Spacer(1, 4))
        flow.append(Paragraph(no_data_msg, styles["Normal"]))
    flow.append(Spacer(1, 14))
    return flow


async def build_exercise_stats_report(
    session, user_id: int, exercise_id: int, custom_period_days: Optional[int] = None
) -> tuple[Path, Path]:
    ex_result = await session.execute(
        select(Exercise).where(Exercise.user_id == user_id, Exercise.id == exercise_id)
    )
    exercise = ex_result.scalar_one_or_none()
    if exercise is None:
        raise ValueError("Упражнение не найдено")

    logs_result = await session.execute(
        select(ExerciseLog)
        .join(WorkoutSession, WorkoutSession.id == ExerciseLog.session_id)
        .options(
            selectinload(ExerciseLog.session),
        )
        .where(
            ExerciseLog.exercise_id == exercise_id,
            WorkoutSession.user_id == user_id,
        )
        .order_by(WorkoutSession.started_at.asc(), ExerciseLog.id.asc())
    )
    logs = list(logs_result.scalars().all())

    expanded: list[SessionSet] = []
    for log in logs:
        at = (log.session.ended_at or log.session.started_at)
        if at is None:
            continue
        repeat_sets = max(1, int(log.sets or 1))
        for set_no in range(1, repeat_sets + 1):
            expanded.append(
                SessionSet(
                    session_id=log.session_id,
                    at=at,
                    weight=float(log.weight_kg),
                    reps=log.reps,
                    set_no=set_no,
                )
            )

    by_session: dict[int, list[SessionSet]] = {}
    for row in expanded:
        by_session.setdefault(row.session_id, []).append(row)
    for rows in by_session.values():
        max_weight = max((r.weight for r in rows), default=0.0)
        threshold = max_weight * 0.5
        for r in rows:
            r.is_working = r.weight > threshold

    all_dates = sorted({x.at.date() for x in expanded})
    range_start = all_dates[0] if all_dates else None
    range_end = all_dates[-1] if all_dates else None
    if range_start is not None and range_end is not None and custom_period_days is not None:
        range_start = max(range_start, range_end - timedelta(days=max(1, custom_period_days) - 1))

    expanded_period = (
        _period_entries(expanded, range_start, range_end) if range_start is not None and range_end is not None else []
    )
    working = [r for r in expanded_period if r.is_working]

    csv_fd, csv_path_raw = tempfile.mkstemp(prefix=f"stats_{exercise_id}_", suffix=".csv")
    pdf_fd, pdf_path_raw = tempfile.mkstemp(prefix=f"stats_{exercise_id}_", suffix=".pdf")
    # descriptor close (Windows needs explicit close before writing by path)
    os.close(csv_fd)
    os.close(pdf_fd)
    csv_path = Path(csv_path_raw)
    pdf_path = Path(pdf_path_raw)

    font_name = _register_font()
    styles = getSampleStyleSheet()
    styles["Normal"].fontName = font_name
    styles["Heading1"].fontName = font_name
    styles["Heading3"].fontName = font_name

    elements: list = [
        Paragraph(f"<b>Статистика по упражнению: {exercise.name}</b>", styles["Heading1"]),
        Spacer(1, 10),
    ]

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")

        if not expanded_period:
            writer.writerow(["Упражнение", "Период", "Комментарий"])
            period_text = (
                f"{range_start:%d.%m.%Y}-{range_end:%d.%m.%Y}"
                if range_start is not None and range_end is not None
                else ""
            )
            writer.writerow([exercise.name, period_text, "Нет данных за выбранный период"])
            elements.append(Paragraph("Нет данных по упражнению за выбранный период.", styles["Normal"]))
        else:
            start_all, end_all = range_start, range_end
            latest = end_all
            week_start = latest - timedelta(days=6)
            month_start = latest.replace(day=1)
            month_end = latest.replace(day=calendar.monthrange(latest.year, latest.month)[1])

            week_entries = _period_entries(working, week_start, latest)
            avg_w, avg_r = _calc_avg(week_entries)
            week_period = f"{week_start:%d.%m.%Y}-{latest:%d.%m.%Y}"
            week_row = [exercise.name, week_period, _fmt_num(avg_w), _fmt_num(avg_r)]
            writer.writerow(["Упражнение", "Период", "Средний вес", "Среднее повторений"])
            writer.writerow(week_row)
            elements.extend(
                _build_block_table(
                    "1. Статистика за неделю",
                    ["Упражнение", "Период", "Средний вес", "Среднее повторений"],
                    week_row,
                    font_name,
                    None if week_entries else "Нет рабочих подходов.",
                )
            )
            writer.writerow([])

            month_entries = _period_entries(working, month_start, month_end)
            month_first, month_last = _first_last(month_entries)
            w_start = month_first.weight if month_first else None
            w_end = month_last.weight if month_last else None
            r_start = month_first.reps if month_first else None
            r_end = month_last.reps if month_last else None
            d_w = (w_end - w_start) if (w_start is not None and w_end is not None) else None
            d_r = (r_end - r_start) if (r_start is not None and r_end is not None) else None
            p_w = _safe_growth(d_w, w_start) if d_w is not None and w_start is not None else None
            p_r = _safe_growth(float(d_r), float(r_start)) if d_r is not None and r_start is not None else None
            month_period = f"{month_start:%d.%m.%Y}-{month_end:%d.%m.%Y}"
            month_row = [
                exercise.name,
                month_period,
                _fmt_num(w_start),
                _fmt_num(w_end),
                _fmt_delta(d_w),
                _fmt_percent(p_w),
                _fmt_num(r_start),
                _fmt_num(r_end),
                _fmt_delta(d_r),
                _fmt_percent(p_r),
            ]
            writer.writerow(
                [
                    "Упражнение",
                    "Период",
                    "Вес начало",
                    "Вес конец",
                    "Рост веса",
                    "Прирост веса %",
                    "Повторы начало",
                    "Повторы конец",
                    "Рост повторений",
                    "Прирост повторений %",
                ]
            )
            writer.writerow(month_row)
            elements.extend(
                _build_block_table(
                    "2. Статистика за месяц",
                    [
                        "Упражнение",
                        "Период",
                        "Вес начало",
                        "Вес конец",
                        "Рост веса",
                        "Прирост веса %",
                        "Повторы начало",
                        "Повторы конец",
                        "Рост повторений",
                        "Прирост повторений %",
                    ],
                    month_row,
                    font_name,
                    None if month_entries else "Нет рабочих подходов.",
                )
            )
            writer.writerow([])

            custom_start = start_all
            custom_end = end_all
            custom_entries = _period_entries(working, custom_start, custom_end)
            custom_first, custom_last = _first_last(custom_entries)
            cw_start = custom_first.weight if custom_first else None
            cw_end = custom_last.weight if custom_last else None
            cr_start = custom_first.reps if custom_first else None
            cr_end = custom_last.reps if custom_last else None
            cd_w = (cw_end - cw_start) if (cw_start is not None and cw_end is not None) else None
            cd_r = (cr_end - cr_start) if (cr_start is not None and cr_end is not None) else None
            cp_w = _safe_growth(cd_w, cw_start) if cd_w is not None and cw_start is not None else None
            cp_r = _safe_growth(float(cd_r), float(cr_start)) if cd_r is not None and cr_start is not None else None
            min_w = min((x.weight for x in custom_entries), default=None)
            max_w = max((x.weight for x in custom_entries), default=None)
            custom_period = f"{custom_start:%d.%m.%Y}-{custom_end:%d.%m.%Y}"
            custom_row = [
                exercise.name,
                custom_period,
                _fmt_num(cw_start),
                _fmt_num(cw_end),
                _fmt_delta(cd_w),
                _fmt_percent(cp_w),
                _fmt_num(cr_start),
                _fmt_num(cr_end),
                _fmt_delta(cd_r),
                _fmt_percent(cp_r),
                _fmt_num(min_w),
                _fmt_num(max_w),
            ]
            writer.writerow(
                [
                    "Упражнение",
                    "Период",
                    "Вес начало",
                    "Вес конец",
                    "Рост веса",
                    "Прирост веса %",
                    "Повторы начало",
                    "Повторы конец",
                    "Рост повторений",
                    "Прирост повторений %",
                    "Вес min",
                    "Вес max",
                ]
            )
            writer.writerow(custom_row)
            elements.extend(
                _build_block_table(
                    "3. Статистика за произвольный период",
                    [
                        "Упражнение",
                        "Период",
                        "Вес начало",
                        "Вес конец",
                        "Рост веса",
                        "Прирост веса %",
                        "Повторы начало",
                        "Повторы конец",
                        "Рост повторений",
                        "Прирост повторений %",
                        "Вес min",
                        "Вес max",
                    ],
                    custom_row,
                    font_name,
                    None if custom_entries else "Нет рабочих подходов.",
                )
            )
            writer.writerow([])

            progress_current_start = latest - timedelta(days=6)
            progress_prev_end = progress_current_start - timedelta(days=1)
            progress_prev_start = progress_prev_end - timedelta(days=6)
            cur_entries = _period_entries(working, progress_current_start, latest)
            prev_entries = _period_entries(working, progress_prev_start, progress_prev_end)
            cur_w, cur_r = _calc_avg(cur_entries)
            prev_w, prev_r = _calc_avg(prev_entries)
            pd_w = (cur_w - prev_w) if (cur_w is not None and prev_w is not None) else None
            pd_r = (cur_r - prev_r) if (cur_r is not None and prev_r is not None) else None
            pp_w = _safe_growth(pd_w, prev_w) if pd_w is not None and prev_w is not None else None
            pp_r = _safe_growth(pd_r, prev_r) if pd_r is not None and prev_r is not None else None
            progress_index = None
            if pp_w is not None and pp_r is not None:
                progress_index = (pp_w + pp_r) / 2
            progress_row = [
                exercise.name,
                f"{progress_current_start:%d.%m}-{latest:%d.%m}",
                f"{progress_prev_start:%d.%m}-{progress_prev_end:%d.%m}",
                _fmt_num(cur_w),
                _fmt_num(prev_w),
                _fmt_delta(pd_w),
                _fmt_percent(pp_w),
                _fmt_num(cur_r),
                _fmt_num(prev_r),
                _fmt_delta(pd_r),
                _fmt_percent(pp_r),
                _fmt_percent(progress_index),
            ]
            writer.writerow(
                [
                    "Упражнение",
                    "Период текущий",
                    "Период предыдущий",
                    "Вес текущий",
                    "Вес предыдущий",
                    "Прирост веса",
                    "Прирост веса %",
                    "Повторы текущие",
                    "Повторы предыдущие",
                    "Прирост повторений",
                    "Прирост повторений %",
                    "Индекс прогресса",
                ]
            )
            writer.writerow(progress_row)
            elements.extend(
                _build_block_table(
                    "4. Прогресс по упражнениям",
                    [
                        "Упражнение",
                        "Период текущий",
                        "Период предыдущий",
                        "Вес текущий",
                        "Вес предыдущий",
                        "Прирост веса",
                        "Прирост веса %",
                        "Повторы текущие",
                        "Повторы предыдущие",
                        "Прирост повторений",
                        "Прирост повторений %",
                        "Индекс прогресса",
                    ],
                    progress_row,
                    font_name,
                    None if (cur_entries and prev_entries) else "Недостаточно рабочих подходов в одном из периодов.",
                )
            )
            writer.writerow([])

            writer.writerow(
                [
                    "Дата",
                    "Упражнение",
                    "Подход 1 Вес",
                    "Подход 1 Повторы",
                    "Подход 2 Вес",
                    "Подход 2 Повторы",
                    "Подход 3 Вес",
                    "Подход 3 Повторы",
                ]
            )
            grouped: dict[date, list[SessionSet]] = {}
            for row in expanded_period:
                grouped.setdefault(row.at.date(), []).append(row)
            summary_rows: list[list[str]] = []
            for day in sorted(grouped):
                day_rows = sorted(grouped[day], key=lambda x: (x.at, x.session_id, x.set_no))
                row: list[str] = [f"{day:%Y-%m-%d}", exercise.name]
                for item in day_rows[:3]:
                    row.extend([_fmt_num(item.weight), _fmt_num(item.reps)])
                while len(row) < 8:
                    row.extend(["", ""])
                row = row[:8]
                writer.writerow(row)
                summary_rows.append(row)

            elements.append(Paragraph("<b>5. Сводная таблица (по датам и подходам)</b>", styles["Heading3"]))
            elements.append(Spacer(1, 8))
            summary_table = Table(
                [
                    [
                        "Дата",
                        "Упражнение",
                        "Подход 1 Вес",
                        "Подход 1 Повторы",
                        "Подход 2 Вес",
                        "Подход 2 Повторы",
                        "Подход 3 Вес",
                        "Подход 3 Повторы",
                    ],
                    *summary_rows,
                ],
                repeatRows=1,
            )
            summary_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("FONTNAME", (0, 0), (-1, -1), font_name),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ]
                )
            )
            elements.append(summary_table)

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        rightMargin=20,
        leftMargin=20,
        topMargin=20,
        bottomMargin=20,
    )
    doc.build(elements)
    if not pdf_path.exists() or pdf_path.stat().st_size < 1024:
        raise RuntimeError("PDF сформирован некорректно")
    return csv_path, pdf_path
