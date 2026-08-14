"""
Calendario rosharano.

Un año tiene 10 meses, cada mes 10 semanas y cada semana 5 días: 500 días.
Los nombres se arman por composición, igual que en el manual:

- mes:  Jes, Nan, Chach, Vev, Palah, Shash, Betab, Kak, Tanat, Ishi
- semana: prefijo del mes + sufijo de la semana (Jes + ach = *Jesach*)
- día:  nombre de la semana + sufijo del día (Jesach + ev = *Jesachev*)

Las columnas de la grilla (el "día de la semana") tienen su propio nombre:
Jesel, Nanel, Chachel, Vevel, Palahel.

Todo se guarda como un **índice absoluto de día** (`day_index`), así pasar días
es una suma y no hay que arrastrar el acarreo de semanas y meses a mano.
"""

MONTHS = ["Jes", "Nan", "Chach", "Vev", "Palah",
          "Shash", "Betab", "Kak", "Tanat", "Ishi"]
WEEK_SUF = ["es", "an", "ach", "ev", "ah", "ash", "ab", "ak", "at", "ish"]
DAY_SUF = ["es", "an", "ach", "ev", "ah"]
WEEKDAYS = ["Jesel", "Nanel", "Chachel", "Vevel", "Palahel"]

DAYS_PER_WEEK = len(DAY_SUF)          # 5
WEEKS_PER_MONTH = len(WEEK_SUF)       # 10
MONTHS_PER_YEAR = len(MONTHS)         # 10
DAYS_PER_MONTH = DAYS_PER_WEEK * WEEKS_PER_MONTH   # 50
DAYS_PER_YEAR = DAYS_PER_MONTH * MONTHS_PER_YEAR   # 500

# Arranque por defecto: 1173.1.1.1 (el "hoy" de El camino de los reyes).
DEFAULT_YEAR = 1173
MAX_YEAR = 9999


def to_index(year: int, month: int, week: int, day: int) -> int:
    """Fecha → índice absoluto de día. Los componentes se recortan al rango."""
    year = max(0, min(MAX_YEAR, int(year)))
    month = max(1, min(MONTHS_PER_YEAR, int(month)))
    week = max(1, min(WEEKS_PER_MONTH, int(week)))
    day = max(1, min(DAYS_PER_WEEK, int(day)))
    return (year * DAYS_PER_YEAR + (month - 1) * DAYS_PER_MONTH
            + (week - 1) * DAYS_PER_WEEK + (day - 1))


DEFAULT_INDEX = to_index(DEFAULT_YEAR, 1, 1, 1)
MAX_INDEX = to_index(MAX_YEAR, MONTHS_PER_YEAR, WEEKS_PER_MONTH, DAYS_PER_WEEK)


def clamp_index(idx) -> int:
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return DEFAULT_INDEX
    return max(0, min(MAX_INDEX, idx))


def from_index(idx: int) -> dict:
    """Índice absoluto → {year, month, week, day} (1-based salvo el año)."""
    idx = clamp_index(idx)
    year, resto = divmod(idx, DAYS_PER_YEAR)
    month, resto = divmod(resto, DAYS_PER_MONTH)
    week, day = divmod(resto, DAYS_PER_WEEK)
    return {"year": year, "month": month + 1, "week": week + 1, "day": day + 1}


def month_name(month: int) -> str:
    return MONTHS[max(1, min(MONTHS_PER_YEAR, int(month))) - 1]


def week_name(month: int, week: int) -> str:
    return month_name(month) + WEEK_SUF[max(1, min(WEEKS_PER_MONTH, int(week))) - 1]


def day_name(month: int, week: int, day: int) -> str:
    return week_name(month, week) + DAY_SUF[max(1, min(DAYS_PER_WEEK, int(day))) - 1]


def weekday_name(day: int) -> str:
    return WEEKDAYS[max(1, min(DAYS_PER_WEEK, int(day))) - 1]


def describe(idx: int) -> dict:
    """Todo lo que hace falta para mostrar una fecha, ya masticado."""
    d = from_index(idx)
    d.update({
        "index": clamp_index(idx),
        "month_name": month_name(d["month"]),
        "week_name": week_name(d["month"], d["week"]),
        "day_name": day_name(d["month"], d["week"], d["day"]),
        "weekday_name": weekday_name(d["day"]),
        # Notación formal del manual: año.mes.semana.día
        "formal": f'{d["year"]}.{d["month"]}.{d["week"]}.{d["day"]}',
    })
    return d


def month_start(year: int, month: int) -> int:
    """Índice del primer día de un mes (útil para dibujar la grilla)."""
    return to_index(year, month, 1, 1)
