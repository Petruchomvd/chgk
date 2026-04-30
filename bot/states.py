"""FSM-стейты для тренировки и генерации статей."""
from aiogram.fsm.state import State, StatesGroup


class TrainingFlow(StatesGroup):
    choosing_mode = State()
    choosing_category = State()
    entering_tournament_query = State()
    choosing_tournament = State()
    choosing_tournament_tour = State()
    in_question = State()
    in_reveal = State()


class StudyFlow(StatesGroup):
    entering_topic = State()
