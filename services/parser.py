"""Парсер строк упражнения: жим 4×8×80, присед 3 подхода по 100 кг, жим 100кг х 10."""

import re
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Модель
# ---------------------------------------------------------------------------


@dataclass
class ParsedExercise:
    """Распарсенная запись упражнения."""

    name: str
    sets: int
    reps: Optional[int]
    weight_kg: float


# Компакт: 4×8×80 (подходы × повторы × вес)
COMPACT_PATTERN = r"(\d+)\s*[×x]\s*(\d+)\s*[×x]\s*(\d+(?:[.,]\d+)?)"

# ---------------------------------------------------------------------------
# Русские числительные → цифры
# ---------------------------------------------------------------------------

_UNITS = {
    "ноль": 0,
    "один": 1,
    "одна": 1,
    "одну": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
}
_TEENS = {
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
}
_TENS = {
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    "девяносто": 90,
}
_HUNDREDS = {
    "сто": 100,
    "двести": 200,
    "триста": 300,
    "четыреста": 400,
    "пятьсот": 500,
}


def _normalize_russian_numbers(text: str) -> str:
    """Преобразует простые русские числительные в цифры: 'три по сто' -> '3 по 100'."""
    words = re.findall(r"\d+(?:[.,]\d+)?|[а-яё]+|[^\s]", text.lower())
    out: list[str] = []
    i = 0
    while i < len(words):
        token = words[i]
        if not re.fullmatch(r"[а-яё]+", token):
            out.append(token)
            i += 1
            continue

        j = i
        value = 0
        used = False
        while j < len(words):
            w = words[j]
            if w == "и" and used:
                j += 1
                continue
            if w in _HUNDREDS:
                value += _HUNDREDS[w]
                used = True
                j += 1
                continue
            if w in _TENS:
                value += _TENS[w]
                used = True
                j += 1
                continue
            if w in _TEENS:
                value += _TEENS[w]
                used = True
                j += 1
                break
            if w in _UNITS:
                value += _UNITS[w]
                used = True
                j += 1
                continue
            break

        if used:
            out.append(str(value))
            i = j
        else:
            out.append(token)
            i += 1

    normalized = " ".join(out)
    normalized = re.sub(r"\s+([,.!?;:])", r"\1", normalized)
    return normalized


# ---------------------------------------------------------------------------
# Современный формат: вес (кг) + повторы, 1 подход
# ---------------------------------------------------------------------------

_NUM = r"(\d+(?:[.,]\d+)?)"
# Маркер единицы веса (после числа). Не используем \b после кг: иначе «100кг_10» ( _ — «буква») не матчится.
_KG_UNIT = r"(?:\s*(?:кг|kgs?|кило|килограмм|kg)(?=[\s_.,:;*/xхX:=\-–\(\[\d+]|$)|кг(?=[^\wа-яё]|$)|(?<=\d)к(?![гa-zA-Z]))"
# После числа повторов: сначала длинные слова и «раз», потом однобуквенные (р|r), иначе «р» съедает «раз».
_REP_WORD = (
    r"(?:раз(?:а|ов|ами)?|повторений?|повторов?|повтор|повт\.?|reps?|rep|р\.?|шт\.?|штук|r)"
)
# Один фрагмент-разделитель между весом и повторами (повторяем * или + сами в шаблоне).
_BETWEEN_ALT = (
    r"(?:"
    r"\s*(?:на|х|x|по|это|on)\s+"
    r"|[\s\.,:;*/xхX\-–_=+>\]\)\[\({}<>\+—–_]+"
    r"|\s*->\s*"
    r")"
)
_BETWEEN_EXPLICIT = r"(?:на|х|x|по|это|on|->|[:/*=]|-)"


def _parse_float(num: str) -> float:
    return float(num.replace(",", "."))


def _ok_weight_reps(w: float, r: int) -> bool:
    return 0 < w < 700 and 0 < r < 10000


def _normalize_tail_for_parse(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    s = s.replace("—", "-").replace("–", "-")
    s = s.replace("×", "x")
    return s


def _match_weight_reps_suffix(suffix: str) -> Optional[tuple[float, int]]:
    """
    Строка целиком — только хвост «числа про вес и повторы» (без названия упражнения).
    Возвращает (вес_кг, повторы) или None.
    """
    s = _normalize_tail_for_parse(suffix)
    if not s:
        return None

    # --- Явный префикс «вес» ---
    m = re.match(
        rf"^вес\s*:?\s*{_NUM}\s*(?:кг|kg|kgs)?\s*(?:{_BETWEEN_ALT})*\s*{_NUM}\s*(?:{_REP_WORD})?\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        w, r = _parse_float(m.group(1)), int(m.group(2))
        if _ok_weight_reps(w, r):
            return w, r

    # «вес 100кг раз 10» / «вес 100кг повторов 10»
    m = re.match(
        rf"^вес\s*:?\s*{_NUM}\s*(?:кг|kg|kgs)?\s+(?:раз|повторов|повторений)\s+{_NUM}\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        w, r = _parse_float(m.group(1)), int(m.group(2))
        if _ok_weight_reps(w, r):
            return w, r

    # --- Склеенные: 100кг10, 100кг10раз ---
    m = re.match(
        rf"^{_NUM}кг({_NUM})(?:{_REP_WORD})?\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        w, r = _parse_float(m.group(1)), int(m.group(2))
        if _ok_weight_reps(w, r):
            return w, r

    m = re.match(
        rf"^{_NUM}kg({_NUM})(?:{_REP_WORD})?\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        w, r = _parse_float(m.group(1)), int(m.group(2))
        if _ok_weight_reps(w, r):
            return w, r

    # --- Число + кг + разделитель + повторы (+ опц. слово про разы) ---
    m = re.match(
        rf"^{_NUM}{_KG_UNIT}\s*_*\s*(?:{_BETWEEN_ALT})*\s*{_NUM}\s*(?:{_REP_WORD})?\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        w, r = _parse_float(m.group(1)), int(m.group(2))
        if _ok_weight_reps(w, r):
            return w, r

    # --- Повторы + разделитель + число с кг (10 x 80кг => вес 80, повторы 10) ---
    m = re.match(
        rf"^{_NUM}\s*(?:{_BETWEEN_ALT})+\s*{_NUM}{_KG_UNIT}\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        r, w = int(m.group(1)), _parse_float(m.group(2))
        if _ok_weight_reps(w, r):
            return w, r

    # --- 100к 10 (к как сокращение кг) ---
    m = re.match(
        rf"^{_NUM}к\s*(?:{_BETWEEN_ALT})*\s*{_NUM}\s*(?:{_REP_WORD})?\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        w, r = _parse_float(m.group(1)), int(m.group(2))
        if _ok_weight_reps(w, r):
            return w, r

    # --- Скобки: 100кг(10), 100(10), 100кг(10раз) ---
    m = re.match(
        rf"^{_NUM}\s*(?:кг|kg|kgs)?\s*\(\s*{_NUM}\s*(?:{_REP_WORD})?\s*\)\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        w, r = _parse_float(m.group(1)), int(m.group(2))
        if _ok_weight_reps(w, r):
            return w, r

    m = re.match(
        rf"^{_NUM}\s*\(\s*{_NUM}\s*(?:{_REP_WORD})?\s*\)\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        w, r = _parse_float(m.group(1)), int(m.group(2))
        if _ok_weight_reps(w, r):
            return w, r

    m = re.match(
        rf"^{_NUM}\s*\[\s*{_NUM}\s*(?:{_REP_WORD})?\s*\]\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        w, r = _parse_float(m.group(1)), int(m.group(2))
        if _ok_weight_reps(w, r):
            return w, r

    # --- Два числа: вес без «кг», повторы с маркером (100 10раз, 100 на 10 раз) ---
    m = re.match(
        rf"^{_NUM}\s+(?:{_BETWEEN_ALT})*\s*{_NUM}\s*(?:{_REP_WORD})\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        w, r = _parse_float(m.group(1)), int(m.group(2))
        if _ok_weight_reps(w, r):
            return w, r

    # --- Два числа с явным разделителем без единиц (17 на 25 => вес 17, повторы 25) ---
    m = re.match(
        rf"^{_NUM}\s*(?:{_BETWEEN_EXPLICIT})\s*{_NUM}\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        w, r = _parse_float(m.group(1)), int(m.group(2))
        if _ok_weight_reps(w, r):
            return w, r

    # --- 100: 10 р, 100/ 10 раз (разделитель сразу после первого числа) ---
    m = re.match(
        rf"^{_NUM}\s*[:/*\-–=]\s*{_NUM}\s*(?:{_REP_WORD})?\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        w, r = _parse_float(m.group(1)), int(m.group(2))
        if _ok_weight_reps(w, r):
            return w, r

    # --- 100 10 — два числа подряд (вес, повторы) ---
    m = re.match(rf"^{_NUM}\s+{_NUM}\s*$", s)
    if m:
        w, r = _parse_float(m.group(1)), int(m.group(2))
        if _ok_weight_reps(w, r):
            return w, r

    return None


def _number_starts(s: str) -> list[int]:
    idxs: list[int] = []
    for i, ch in enumerate(s):
        if ch.isdigit() and (i == 0 or not s[i - 1].isdigit()):
            idxs.append(i)
    return idxs


def _tail_candidate_starts(t: str) -> list[int]:
    """Индексы начала хвоста: первое число записи или слово «вес» перед числами."""
    idxs = list(_number_starts(t))
    for m in re.finditer(r"(?<![а-яёa-z])вес\s*:?\s*(?=\d)", t, re.IGNORECASE):
        idxs.append(m.start())
    return sorted(set(idxs))


def _try_modern_weight_reps_line(text: str) -> Optional[ParsedExercise]:
    """
    Форматы с явным весом и повторениями в одной «табличной» записи, 1 подход.
    Название — всё, что слева от последнего удачно разобранного хвоста.
    """
    t = text.strip()
    if not t:
        return None

    best: Optional[tuple[int, float, int]] = None  # start_index, w, r
    for i in _tail_candidate_starts(t):
        suffix = t[i:].strip()
        got = _match_weight_reps_suffix(suffix)
        if got is None:
            continue
        w, r = got
        if best is None or i < best[0]:
            best = (i, w, r)

    if best is None:
        return None

    start_i, w, r = best
    name = t[:start_i].strip()
    if not name:
        return None
    return ParsedExercise(name=name, sets=1, reps=r, weight_kg=w)


def _has_legacy_sets_keyword(text: str) -> bool:
    """Явные «подходы/сеты» — старая грамматика, не перехватываем табличным парсером."""
    return bool(
        re.search(
            r"\d+\s*(?:подход[а-я]*|сет[а-я]*)\b",
            text,
            re.IGNORECASE,
        )
    )


# ---------------------------------------------------------------------------
# Старый комбинированный разбор (подходы, «по», разные порядки)
# ---------------------------------------------------------------------------


def _parse_exercise_line_legacy(text: str) -> Optional[ParsedExercise]:
    weight_match = re.search(
        r"(?:по|с)\s*(\d+(?:[.,]\d+)?)\s*(?:кг)?|(\d+(?:[.,]\d+)?)\s*кг",
        text,
        re.IGNORECASE,
    )
    weight = 0.0
    if weight_match:
        w = weight_match.group(1) or weight_match.group(2)
        weight = float(w.replace(",", "."))

    sets_match = re.search(
        r"(\d+)\s*(?:подход[а-я]*|сет[а-я]*|×|x)",
        text,
        re.IGNORECASE,
    )
    sets = 1
    if sets_match:
        sets = int(sets_match.group(1))

    reps_match = re.search(
        r"(?:по|на)\s*(\d+)\s*(?:раз|повторений?|повт\.?)|(\d+)\s*(?:раз|повторений?|повт\.?)",
        text,
        re.IGNORECASE,
    )
    reps: Optional[int] = None
    if reps_match:
        reps = int(reps_match.group(1) or reps_match.group(2))

    alt_reps = re.search(r"(\d+)\s+(?:по|на)\s+(\d+(?:[.,]\d+)?)", text)
    if alt_reps and not reps:
        n_val = int(alt_reps.group(1))
        m_val = float(alt_reps.group(2).replace(",", "."))
        if weight == m_val or (weight == 0 and m_val <= 500):
            sets = 1
            reps = n_val
            if weight == 0:
                weight = m_val
        else:
            sets = n_val
            reps = int(m_val)

    name_pattern = re.compile(
        r"^(.+?)(?=\d+\s*(?:подход|сет|×|x|по|с|\d))",
        re.IGNORECASE | re.DOTALL,
    )
    name_match = name_pattern.match(text)
    if name_match:
        name = name_match.group(1).strip()
    else:
        name = re.sub(
            r"\d+\s*(?:подход[а-я]*|сет[а-я]*|×|x|по|с|кг|раз|повторений?\.?)?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        name = re.sub(r"\s+", " ", name)

    if not name or (weight == 0 and sets == 1 and not reps):
        return None

    return ParsedExercise(name=name, sets=sets, reps=reps, weight_kg=weight)


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------


def parse_exercise_line(text: str) -> Optional[ParsedExercise]:
    """
    Парсит строку вида:
    - жим лёжа 4×8×80
    - присед 3 подхода по 100 кг
    - жим 100кг х 10, 100 kg x 10 reps, вес 100 10, 100 10 раз
    """
    text_raw = text.strip()
    if not text_raw:
        return None

    # Компакт с цифрами — до нормализации (она ломает латинские kg, x и т.п.)
    compact_match = re.search(COMPACT_PATTERN, text_raw, re.IGNORECASE)
    if compact_match:
        sets = int(compact_match.group(1))
        reps = int(compact_match.group(2))
        weight = float(compact_match.group(3).replace(",", "."))
        name = text_raw[: compact_match.start()].strip()
        if name:
            return ParsedExercise(name=name, sets=sets, reps=reps, weight_kg=weight)

    # Табличный вес×повторы — до нормализации (слова «раз» и латиница)
    if not _has_legacy_sets_keyword(text_raw):
        modern = _try_modern_weight_reps_line(text_raw)
        if modern is not None:
            return modern

    text = _normalize_russian_numbers(text_raw)

    compact_match = re.search(COMPACT_PATTERN, text, re.IGNORECASE)
    if compact_match:
        sets = int(compact_match.group(1))
        reps = int(compact_match.group(2))
        weight = float(compact_match.group(3).replace(",", "."))
        name = text[: compact_match.start()].strip()
        if name:
            return ParsedExercise(name=name, sets=sets, reps=reps, weight_kg=weight)

    return _parse_exercise_line_legacy(text)


if __name__ == "__main__":
    samples = [
        "жим 100кг 10",
        "жим 100кг на 10",
        "жим 100кг х 10",
        "жим 100 кг 10",
        "жим 100kg x 10",
        "жим 100 kg * 10",
        "жим вес 100 10",
        "жим вес: 100 10",
        "жим 100кг_10",
        "жим 100кг(10)",
        "жим 100к 10",
        "жим 100 10раз",
        "жим 100 на 10 раз",
        "жим 100 10 reps",
        "жим 100кг 10раз",
        "жим 100 кг / 10 р",
        "жим 100кг это 10раз",
        "жим вес 100кг на 10раз",
        "присед 3 подхода по 100 кг",
    ]
    for s in samples:
        p = parse_exercise_line(s)
        print(s, "->", p)
