from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Callback data prefixes
CB_MAIN = "main"
CB_EXERCISES = "ex"
CB_TEMPLATES = "tmpl"
CB_WORKOUT = "wo"
CB_DELETE = "del"
CB_EDIT = "edit"
CB_ADD = "add"
CB_BACK = "back"
CB_SELECT = "sel"
CB_DONE = "done"


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1. Создать тренировку", callback_data=f"{CB_TEMPLATES}:create")],
        [InlineKeyboardButton("2. Редактировать / удалить тренировку", callback_data=f"{CB_TEMPLATES}:list")],
        [InlineKeyboardButton("3. Создать / редактировать упражнения", callback_data=f"{CB_EXERCISES}:list")],
        [InlineKeyboardButton("4. Запустить тренировку", callback_data=f"{CB_WORKOUT}:start")],
        [InlineKeyboardButton("5. Статистика", callback_data=f"{CB_MAIN}:stats")],
    ])


def back_to_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀ В главное меню", callback_data=f"{CB_MAIN}:menu")],
    ])


def exercise_after_add_keyboard() -> InlineKeyboardMarkup:
    """После добавления упражнения: добавить ещё или в меню."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить ещё упражнение", callback_data=f"{CB_EXERCISES}:add")],
        [InlineKeyboardButton("◀ В главное меню", callback_data=f"{CB_MAIN}:menu")],
    ])


def exercises_list_keyboard(exercises: list, page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    """Клавиатура со списком упражнений."""
    buttons = []
    start = page * per_page
    chunk = exercises[start : start + per_page]
    for ex in chunk:
        label = f"{ex.name} ({getattr(ex, 'body_part', 'Другое')})"
        buttons.append([
            InlineKeyboardButton(label, callback_data=f"{CB_EXERCISES}:view:{ex.id}"),
        ])
    buttons.append([InlineKeyboardButton("➕ Добавить упражнение", callback_data=f"{CB_EXERCISES}:add")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Назад", callback_data=f"{CB_EXERCISES}:list:{page - 1}"))
    if start + per_page < len(exercises):
        nav.append(InlineKeyboardButton("Далее ▶", callback_data=f"{CB_EXERCISES}:list:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("◀ В главное меню", callback_data=f"{CB_MAIN}:menu")])
    return InlineKeyboardMarkup(buttons)


def exercise_detail_keyboard(exercise_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"{CB_EXERCISES}:{CB_DELETE}:{exercise_id}")],
        [InlineKeyboardButton("◀ К списку", callback_data=f"{CB_EXERCISES}:list")],
    ])


def templates_list_keyboard(templates: list, page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    buttons = []
    start = page * per_page
    chunk = templates[start : start + per_page]
    for t in chunk:
        buttons.append([
            InlineKeyboardButton(t.name, callback_data=f"{CB_TEMPLATES}:view:{t.id}"),
        ])
    buttons.append([InlineKeyboardButton("➕ Создать тренировку", callback_data=f"{CB_TEMPLATES}:create")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Назад", callback_data=f"{CB_TEMPLATES}:list:{page - 1}"))
    if start + per_page < len(templates):
        nav.append(InlineKeyboardButton("Далее ▶", callback_data=f"{CB_TEMPLATES}:list:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("◀ В главное меню", callback_data=f"{CB_MAIN}:menu")])
    return InlineKeyboardMarkup(buttons)


def template_detail_keyboard(template_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить упражнение", callback_data=f"{CB_TEMPLATES}:add_ex:{template_id}")],
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"{CB_TEMPLATES}:{CB_DELETE}:{template_id}")],
        [InlineKeyboardButton("▶ Запустить", callback_data=f"{CB_WORKOUT}:start:{template_id}")],
        [
            InlineKeyboardButton("◀ К списку", callback_data=f"{CB_TEMPLATES}:list"),
            InlineKeyboardButton("🏠 В главное меню", callback_data=f"{CB_MAIN}:menu"),
        ],
    ])


def template_add_exercise_choose_keyboard(template_id: int) -> InlineKeyboardMarkup:
    """Первый выбор: часть тела или создать новое."""
    buttons = [[InlineKeyboardButton(part, callback_data=f"{CB_TEMPLATES}:add_ex_body:{template_id}:{i}")] for i, part in enumerate(BODY_PARTS)]
    buttons.append([InlineKeyboardButton("➕ Создать новое упражнение", callback_data=f"{CB_TEMPLATES}:add_ex_new:{template_id}")])
    buttons.append([InlineKeyboardButton("◀ Назад", callback_data=f"{CB_TEMPLATES}:view:{template_id}")])
    return InlineKeyboardMarkup(buttons)


def template_add_exercise_by_body_keyboard(
    template_id: int, body_part_idx: int, exercises: list, added_ids: set
) -> InlineKeyboardMarkup:
    """Упражнения по выбранной части тела + создать новое."""
    buttons = []
    for ex in exercises:
        if ex.id not in added_ids:
            buttons.append([
                InlineKeyboardButton(ex.name, callback_data=f"{CB_TEMPLATES}:add_ex_sel:{template_id}:{ex.id}"),
            ])
    if not buttons:
        buttons.append([InlineKeyboardButton("(нет упражнений)", callback_data="_")])
    buttons.append([
        InlineKeyboardButton(
            f"➕ Создать новое ({BODY_PARTS[body_part_idx]})",
            callback_data=f"{CB_TEMPLATES}:add_ex_new_body:{template_id}:{body_part_idx}",
        ),
    ])
    buttons.append([InlineKeyboardButton("◀ Назад", callback_data=f"{CB_TEMPLATES}:add_ex:{template_id}")])
    return InlineKeyboardMarkup(buttons)


def template_delete_confirm_keyboard(template_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, удалить", callback_data=f"{CB_TEMPLATES}:{CB_DELETE}:confirm:{template_id}"),
            InlineKeyboardButton("❌ Нет", callback_data=f"{CB_TEMPLATES}:view:{template_id}"),
        ],
    ])


def workout_template_select_keyboard(templates: list) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton("🆓 Свободная тренировка", callback_data=f"{CB_WORKOUT}:free")]]
    for t in templates:
        buttons.append([InlineKeyboardButton(t.name, callback_data=f"{CB_WORKOUT}:use:{t.id}")])
    buttons.append([InlineKeyboardButton("◀ Отмена", callback_data=f"{CB_MAIN}:menu")])
    return InlineKeyboardMarkup(buttons)


def workout_in_progress_keyboard(session_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↩ Отменить последнюю запись", callback_data=f"{CB_WORKOUT}:undo:{session_id}")],
        [InlineKeyboardButton("✅ Завершить тренировку", callback_data=f"{CB_WORKOUT}:{CB_DONE}:{session_id}")],
        [InlineKeyboardButton("◀ Отмена", callback_data=f"{CB_MAIN}:menu")],
    ])


BODY_PARTS = ("Ноги", "Грудь", "Спина", "Плечи", "Руки", "Пресс", "Другое")


def exercise_pick_or_create_keyboard(matches: list) -> InlineKeyboardMarkup:
    """Совпадения + создать всё равно."""
    buttons = []
    for ex in matches[:8]:
        label = f"{ex.name} ({getattr(ex, 'body_part', 'Другое')})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"{CB_EXERCISES}:use:{ex.id}")])
    buttons.append([InlineKeyboardButton("➕ Создать всё равно", callback_data=f"{CB_EXERCISES}:create_anyway")])
    buttons.append([InlineKeyboardButton("◀ Отмена", callback_data=f"{CB_EXERCISES}:list")])
    return InlineKeyboardMarkup(buttons)


def exercise_body_part_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора части тела при создании упражнения."""
    buttons = [[InlineKeyboardButton(part, callback_data=f"{CB_EXERCISES}:body:{i}")] for i, part in enumerate(BODY_PARTS)]
    return InlineKeyboardMarkup(buttons)


def template_exercise_pick_or_create_keyboard(template_id: int, matches: list) -> InlineKeyboardMarkup:
    """Совпадения + создать всё равно (в контексте шаблона)."""
    buttons = []
    for ex in matches[:8]:
        label = f"{ex.name} ({getattr(ex, 'body_part', 'Другое')})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"{CB_TEMPLATES}:use_ex:{template_id}:{ex.id}")])
    buttons.append([InlineKeyboardButton("➕ Создать всё равно", callback_data=f"{CB_TEMPLATES}:create_anyway:{template_id}")])
    buttons.append([InlineKeyboardButton("◀ Отмена", callback_data=f"{CB_TEMPLATES}:view:{template_id}")])
    return InlineKeyboardMarkup(buttons)


def template_new_exercise_body_part_keyboard(template_id: int) -> InlineKeyboardMarkup:
    """Клавиатура выбора части тела при создании упражнения из шаблона."""
    buttons = [
        [InlineKeyboardButton(part, callback_data=f"{CB_TEMPLATES}:ex_body:{template_id}:{i}")]
        for i, part in enumerate(BODY_PARTS)
    ]
    return InlineKeyboardMarkup(buttons)


def workout_pick_exercise_keyboard(
    session_id: int, exercises: list, parsed_name: str
) -> InlineKeyboardMarkup:
    """Выбор упражнения при нескольких совпадениях или создание нового."""
    buttons = []
    for ex in exercises[:8]:
        label = f"{ex.name} ({getattr(ex, 'body_part', 'Другое')})"
        buttons.append([
            InlineKeyboardButton(label, callback_data=f"{CB_WORKOUT}:pick_ex:{session_id}:{ex.id}"),
        ])
    buttons.append([
        InlineKeyboardButton(f"➕ Создать «{parsed_name}»", callback_data=f"{CB_WORKOUT}:pick_new:{session_id}"),
    ])
    return InlineKeyboardMarkup(buttons)


def workout_confirm_create_exercise_keyboard(session_id: int, parsed_name: str) -> InlineKeyboardMarkup:
    """Подтверждение создания нового упражнения."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, создать", callback_data=f"{CB_WORKOUT}:pick_new:{session_id}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"{CB_WORKOUT}:pick_cancel:{session_id}"),
        ],
    ])


def workout_voice_review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Сохранить", callback_data=f"{CB_WORKOUT}:voice_confirm"),
            InlineKeyboardButton("✏ Исправить", callback_data=f"{CB_WORKOUT}:voice_edit"),
        ],
        [InlineKeyboardButton("❌ Отменить", callback_data=f"{CB_WORKOUT}:voice_cancel")],
    ])
