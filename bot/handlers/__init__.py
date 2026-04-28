from bot.handlers.common import setup_common_handlers
from bot.handlers.exercises import setup_exercise_handlers
from bot.handlers.stats import setup_stats_handlers
from bot.handlers.templates import setup_template_handlers
from bot.handlers.workout import setup_workout_handlers


def setup_handlers(application):
    setup_common_handlers(application)
    setup_exercise_handlers(application)
    setup_template_handlers(application)
    setup_workout_handlers(application)
    setup_stats_handlers(application)
