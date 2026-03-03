from enum import Enum, auto


class States(Enum):
    MAIN = auto()
    EXERCISE_NAME = auto()
    EXERCISE_PICK_OR_CREATE = auto()
    EXERCISE_BODY_PART = auto()
    TEMPLATE_NAME = auto()
    TEMPLATE_DESC = auto()
    TEMPLATE_ADD_EXERCISE = auto()
    TEMPLATE_NEW_EXERCISE_NAME = auto()
    TEMPLATE_NEW_EXERCISE_PICK_OR_CREATE = auto()
    TEMPLATE_NEW_EXERCISE_BODY_PART = auto()
    WORKOUT_INPUT = auto()
