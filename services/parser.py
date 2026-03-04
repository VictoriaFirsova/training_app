"""Парсер текстовых фраз вида: жим 4×8×80, присед 3 подхода по 100 кг."""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedExercise:
    """Распарсенная запись упражнения."""

    name: str
    sets: int
    reps: Optional[int]
    weight_kg: float


# Паттерны:
# жим 4×8×80  или  4x8x80  (sets x reps x weight)
# присед 3 подхода по 100
# жим 4 по 80 (без повторений)
# бицепс 4 сета 12 раз по 15
WEIGHT_PATTERN = r"(?:по|с)\s*(\d+(?:[.,]\d+)?)\s*(?:кг)?|(\d+(?:[.,]\d+)?)\s*кг"
REPS_PATTERN = r"(?:по|на)\s*(\d+)\s*(?:раз|повторений?|повт\.?)|(\d+)\s*(?:раз|повторений?|повт\.?)"
SETS_PATTERN = r"(\d+)\s*(?:подход[а-я]*|сет[а-я]*|×|x)"
COMPACT_PATTERN = r"(\d+)\s*[×x]\s*(\d+)\s*[×x]\s*(\d+(?:[.,]\d+)?)"  # 4×8×80

_UNITS = {
    "ноль": 0,
    "один": 1,
    "одна": 1,
    "одну": 1,
    "раз": 1,
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


def parse_exercise_line(text: str) -> Optional[ParsedExercise]:
    """
    Парсит строку вида:
    - жим лёжа 4×8×80
    - присед 3 подхода по 100 кг
    - становая 5×5×120
    - бицепс 4 сета 12 раз по 15
    """
    text = text.strip()
    if not text:
        return None
    text = _normalize_russian_numbers(text)

    # Сначала пробуем компактный формат: упражнение 4×8×80
    compact_match = re.search(COMPACT_PATTERN, text, re.IGNORECASE)
    if compact_match:
        sets = int(compact_match.group(1))
        reps = int(compact_match.group(2))
        weight = float(compact_match.group(3).replace(",", "."))
        name = text[: compact_match.start()].strip()
        if name:
            return ParsedExercise(name=name, sets=sets, reps=reps, weight_kg=weight)

    # Ищем вес
    weight_match = re.search(
        r"(?:по|с)\s*(\d+(?:[.,]\d+)?)\s*(?:кг)?|(\d+(?:[.,]\d+)?)\s*кг",
        text,
        re.IGNORECASE,
    )
    weight = 0.0
    if weight_match:
        w = weight_match.group(1) or weight_match.group(2)
        weight = float(w.replace(",", "."))

    # Ищем подходы
    sets_match = re.search(
        r"(\d+)\s*(?:подход[а-я]*|сет[а-я]*|×|x)",
        text,
        re.IGNORECASE,
    )
    sets = 1
    if sets_match:
        sets = int(sets_match.group(1))

    # Ищем повторения
    reps_match = re.search(
        r"(?:по|на)\s*(\d+)\s*(?:раз|повторений?|повт\.?)|(\d+)\s*(?:раз|повторений?|повт\.?)",
        text,
        re.IGNORECASE,
    )
    reps = None
    if reps_match:
        reps = int(reps_match.group(1) or reps_match.group(2))

    # "N по M" / "N на M" — два варианта:
    # 1) N повторений по M кг (1 подход): жим 5 по 15, жим 25 на 30
    # 2) N подходов по M повторений: 3 по 10 с 100
    # Если вес = M (второе число), то M — это вес, N — повторения, 1 подход.
    # Если есть отдельно "с X" / "X кг", то N — подходы, M — повторения.
    alt_reps = re.search(r"(\d+)\s+(?:по|на)\s+(\d+(?:[.,]\d+)?)", text)
    if alt_reps and not reps:
        n_val = int(alt_reps.group(1))
        m_val = float(alt_reps.group(2).replace(",", "."))
        # Вес уже найден из "по M" — значит M это вес, N это повторения
        if weight == m_val or (weight == 0 and m_val <= 500):  # 500 — разумный макс вес в кг
            sets = 1
            reps = n_val
            if weight == 0:
                weight = m_val
        else:
            sets = n_val
            reps = int(m_val)

    # Название — всё до первого числа или до "подход/сет/по/с"
    name_pattern = re.compile(
        r"^(.+?)(?=\d+\s*(?:подход|сет|×|x|по|с|\d))",
        re.IGNORECASE | re.DOTALL,
    )
    name_match = name_pattern.match(text)
    if name_match:
        name = name_match.group(1).strip()
    else:
        # Убираем числа и служебные слова
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
