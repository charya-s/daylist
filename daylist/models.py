from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class TaskList(models.Model):
    class Color(models.TextChoices):
        DEFAULT = "default", "Default"
        RED = "red", "Red"
        ORANGE = "orange", "Orange"
        YELLOW = "yellow", "Yellow"
        GREEN = "green", "Green"
        BLUE = "blue", "Blue"
        PURPLE = "purple", "Purple"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=80)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    color = models.CharField(max_length=7, choices=Color.choices, default=Color.DEFAULT)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="unique_task_list_name")
        ]

    def __str__(self):
        return self.name


class Task(models.Model):
    class MatrixQuadrant(models.TextChoices):
        AUTO = "", "Automatic (flag and due date)"
        DO = "do", "Do first"
        SCHEDULE = "schedule", "Schedule"
        BATCH = "batch", "Delegate or batch"
        RECONSIDER = "reconsider", "Reconsider"

    class Repeat(models.TextChoices):
        NEVER = "", "Does not repeat"
        DAILY = "daily", "Days"
        WEEKLY = "weekly", "Weeks"
        MONTHLY = "monthly", "Months"
        YEARLY = "yearly", "Years"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    due = models.DateField(null=True, blank=True, db_index=True)
    done = models.BooleanField(default=False)
    task_list = models.ForeignKey(TaskList, on_delete=models.SET_NULL, null=True,
                                  blank=True, related_name="tasks")
    restore_folder = models.ForeignKey(TaskList, on_delete=models.SET_NULL, null=True, blank=True, related_name="restorable_tasks")
    notes = models.TextField(blank=True, default="")
    flagged = models.BooleanField(default=False)
    matrix_quadrant = models.CharField(max_length=10, choices=MatrixQuadrant.choices, blank=True, default="")
    repeat = models.CharField(max_length=7, choices=Repeat.choices, blank=True, default="")
    repeat_every = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(365)])
    repeat_anchor = models.DateField(null=True, blank=True)
    next_occurrence = models.OneToOneField("self", on_delete=models.SET_NULL, null=True,
                                          blank=True, related_name="previous_occurrence")


    @property
    def repeat_label(self):
        if not self.repeat:
            return "Does not repeat"
        unit = {"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}[self.repeat]
        return f"Every {unit}" if self.repeat_every == 1 else f"Every {self.repeat_every} {unit}s"


class Subtask(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="subtasks")
    title = models.CharField(max_length=200)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    due = models.DateField(null=True, blank=True)
    done = models.BooleanField(default=False)

    class Meta:
        ordering = ["pk"]
