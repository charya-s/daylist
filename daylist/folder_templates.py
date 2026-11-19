from django import forms
from django.db import transaction
from django.forms import formset_factory

from .models import Subtask, Task, TaskListTemplate


class TemplateForm(forms.ModelForm):
    class Meta:
        model = TaskListTemplate
        fields = ["name", "color"]
        widgets = {"color": forms.RadioSelect}


class TemplateTaskForm(forms.Form):
    title = forms.CharField(max_length=200, label="Task name", widget=forms.TextInput(attrs={"class": "template-panel-title", "placeholder": "Task name"}))
    notes = forms.CharField(required=False, max_length=10000, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Add notes…"}))
    flagged = forms.BooleanField(required=False, label="Priority")
    subtasks = forms.CharField(required=False, max_length=20000, label="Subtasks (one per line)", widget=forms.Textarea(attrs={"rows": 3}))

    def clean_subtasks(self):
        titles = [line.strip() for line in self.cleaned_data["subtasks"].splitlines() if line.strip()]
        if len(titles) > 100 or any(len(title) > 200 for title in titles):
            raise forms.ValidationError("Use up to 100 subtasks, with 200 characters or fewer per name.")
        return titles


TemplateTasks = formset_factory(TemplateTaskForm, extra=0, min_num=1, validate_min=True,
                               max_num=500, validate_max=True, absolute_max=600, can_delete=True)


TaskTemplateTasks = formset_factory(TemplateTaskForm, extra=0, min_num=1, validate_min=True,
                                   max_num=1, validate_max=True, absolute_max=600, can_delete=True)


def task_snapshot(task):
    return {"title": task.title, "notes": task.notes, "flagged": task.flagged,
            "subtasks": [step.title for step in task.subtasks.filter(deleted_at__isnull=True)]}


@transaction.atomic
def instantiate_task_template(owner, template, folder=None, due=None):
    if template.owner_id != owner.pk or template.kind != TaskListTemplate.Kind.TASK or len(template.tasks) != 1:
        raise ValueError("Choose one of your task templates.")
    if folder and folder.owner_id != owner.pk:
        raise ValueError("Choose one of your folders.")
    item = template.tasks[0]
    task = Task.objects.create(owner=owner, task_list=folder, title=item["title"],
                               notes=item.get("notes", ""), flagged=item.get("flagged", False), due=due)
    Subtask.objects.bulk_create([Subtask(task=task, title=title) for title in item.get("subtasks", [])])
    return task


def folder_snapshot(folder):
    tasks = Task.objects.filter(owner=folder.owner, task_list=folder, done=False, deleted_at__isnull=True).order_by("position", "-pk").prefetch_related("subtasks")
    return [{"title": task.title, "notes": task.notes, "flagged": task.flagged,
             "subtasks": [step.title for step in task.subtasks.all() if step.deleted_at is None]} for task in tasks]


def editor_initial(items):
    return [{**item, "subtasks": "\n".join(item.get("subtasks", []))} for item in items]


@transaction.atomic
def instantiate_template(folder, template):
    if template.owner_id != folder.owner_id or template.kind != TaskListTemplate.Kind.FOLDER:
        raise ValueError("Template and folder must have the same owner.")
    for position, item in enumerate(template.tasks, start=1):
        task = Task.objects.create(owner=folder.owner, task_list=folder, title=item["title"],
                                   notes=item.get("notes", ""), flagged=item.get("flagged", False), position=position)
        Subtask.objects.bulk_create([Subtask(task=task, title=title) for title in item.get("subtasks", [])])
