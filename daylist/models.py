from django.conf import settings
from django.db import models


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
    color = models.CharField(max_length=7, choices=Color.choices, default=Color.DEFAULT)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="unique_task_list_name")
        ]

    def __str__(self):
        return self.name


class Task(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    due = models.DateField(null=True, blank=True, db_index=True)
    done = models.BooleanField(default=False)
    task_list = models.ForeignKey(TaskList, on_delete=models.SET_NULL, null=True,
                                  blank=True, related_name="tasks")
    notes = models.TextField(blank=True, default="")
    flagged = models.BooleanField(default=False)


class Subtask(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="subtasks")
    title = models.CharField(max_length=200)
    due = models.DateField(null=True, blank=True)
    done = models.BooleanField(default=False)

    class Meta:
        ordering = ["pk"]
