from calendar import monthrange
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Subtask, Task


def next_due_date(task, today):
    base = task.due or today
    every = task.repeat_every
    try:
        if task.repeat in (Task.Repeat.DAILY, Task.Repeat.WEEKLY):
            days = every * (7 if task.repeat == Task.Repeat.WEEKLY else 1)
            steps = max(1, (today - base).days // days + 1)
            return base + timedelta(days=steps * days)
        months = every * (12 if task.repeat == Task.Repeat.YEARLY else 1)
        start = base.year * 12 + base.month - 1
        current = today.year * 12 + today.month - 1
        steps = max(1, (current - start) // months)
        day = (task.repeat_anchor or base).day
        while True:
            year, month = divmod(start + steps * months, 12)
            month += 1
            result = date(year, month, min(day, monthrange(year, month)[1]))
            if result > today:
                return result
            steps += 1
    except (ValueError, OverflowError):
        raise ValidationError("Choose an earlier due date to repeat this task.")


@transaction.atomic
def complete_task(task, today):
    # Claim the completion before reading the repeat settings. Repeated requests
    # and reopening an earlier occurrence must not create extra copies.
    changed = Task.objects.filter(pk=task.pk, done=False, deleted_at__isnull=True).update(done=True)
    task.refresh_from_db()
    if not changed or not task.repeat or task.next_occurrence_id:
        return
    due = next_due_date(task, today)
    delta = due - (task.due or today)
    steps = list(task.subtasks.filter(deleted_at__isnull=True))
    try:
        step_dates = [step.due + delta if step.due else None for step in steps]
    except OverflowError:
        raise ValidationError("Choose earlier subtask dates to repeat this task.")
    following = Task.objects.create(
        owner=task.owner, title=task.title, due=due, task_list=task.task_list, restore_folder=task.restore_folder,
        notes=task.notes, flagged=task.flagged, matrix_quadrant=task.matrix_quadrant, repeat=task.repeat,
        repeat_every=task.repeat_every, repeat_anchor=task.repeat_anchor or task.due or today,
    )
    Subtask.objects.bulk_create([
        Subtask(task=following, title=step.title, due=step_due)
        for step, step_due in zip(steps, step_dates)
    ])
    Task.objects.filter(pk=task.pk).update(next_occurrence=following)
