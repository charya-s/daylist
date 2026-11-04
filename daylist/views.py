import re
from datetime import timedelta

from django import forms
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.timezone import localdate
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from .models import Subtask, Task, TaskList


class DateInput(forms.DateInput):
    input_type = "date"


class QuickTaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["title", "due"]
        widgets = {"due": forms.HiddenInput(attrs={"id": "quick-due"})}


class NotesForm(forms.ModelForm):
    notes = forms.CharField(required=False, max_length=10000, strip=False,
                           widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Add notes…", "maxlength": 10000}))

    class Meta:
        model = Task
        fields = ["notes"]


class TaskForm(forms.ModelForm):

    class Meta:
        model = Task
        fields = ["title", "due", "task_list"]
        widgets = {"due": DateInput(format="%Y-%m-%d")}

    def __init__(self, lists, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["task_list"].queryset = lists
        self.fields["task_list"].label = "Folder"
        self.fields["task_list"].empty_label = "No folder"


class TitleForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["title"]


class FolderForm(TaskForm):
    class Meta(TaskForm.Meta):
        fields = ["task_list"]


class SubtaskForm(forms.ModelForm):
    class Meta:
        model = Subtask
        fields = ["title", "due"]
        widgets = {"due": DateInput(format="%Y-%m-%d")}


class ListForm(forms.ModelForm):
    class Meta:
        model = TaskList
        fields = ["name"]


class NewFolderForm(ListForm):
    color = forms.ChoiceField(choices=TaskList.Color.choices, required=False,
                              initial=TaskList.Color.DEFAULT, widget=forms.RadioSelect)

    class Meta(ListForm.Meta):
        fields = ["name", "color"]

    def clean_color(self):
        return self.cleaned_data["color"] or TaskList.Color.DEFAULT



@never_cache
@require_http_methods(["GET", "POST"])
def sign_in(request):
    if request.user.is_authenticated:
        return redirect("all")
    form = AuthenticationForm(request, data=request.POST if request.method == "POST" else None)
    limited = False
    if request.method == "POST":
        # A single worker shares this short-lived cap across its threads.
        key = "sign-in-attempts"
        cache.add(key, 0, timeout=60)
        try:
            limited = cache.incr(key) > 10
        except ValueError:
            limited = True
        if not limited and form.is_valid():
            login(request, form.get_user())
            cache.delete(key)
            return redirect("all")
    return render(request, "login.html", {"form": form, "limited": limited},
                  status=429 if limited else 200)


@require_POST
def sign_out(request):
    logout(request)
    return redirect("sign-in")



def valid_id(value):
    return int(value) if re.fullmatch(r"[0-9]{1,18}", value or "") else 0


def group_tasks(tasks, today):
    groups = {name: [] for name in ("Overdue", "Today", "Upcoming", "No date", "Completed")}
    for task in tasks:
        if task.done:
            group = "Completed"
        elif task.due is None:
            group = "No date"
        elif task.due < today:
            group = "Overdue"
        elif task.due == today:
            group = "Today"
        else:
            group = "Upcoming"
        groups[group].append(task)
    return [(name, items) for name, items in groups.items() if items]


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def tasks(request, today_only=False, list_id=None):
    today = localdate()
    quick_dates = {
        "today": today, "tomorrow": today + timedelta(days=1),
        "next-week": today + timedelta(days=7), "no-date": None,
    }
    owned = Task.objects.filter(owner=request.user)
    lists = TaskList.objects.filter(owner=request.user)
    active_list = get_object_or_404(lists, pk=list_id) if list_id else None
    visible = owned
    if active_list:
        visible = owned.filter(task_list=active_list)
        view_url = reverse("task-list", args=[active_list.pk])
    elif today_only:
        visible = owned.filter(due=today)
        view_url = reverse("today")
    else:
        view_url = reverse("all")

    selected_id = valid_id(request.GET.get("task"))
    selected = get_object_or_404(visible, pk=selected_id) if selected_id else None
    quick_form = QuickTaskForm(initial={"due": today} if today_only else None)
    add_list_form = NewFolderForm(prefix="new-list")
    rename_form = ListForm(instance=active_list, prefix="rename") if active_list else None
    title_form = folder_form = None
    notes_form = None
    add_subtask_form = SubtaskForm(prefix="new-step")
    subtask_form = None
    edited_subtask_id = None

    def back(task_id=None):
        return redirect(f"{view_url}?task={task_id}" if task_id else view_url)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            quick_form = QuickTaskForm(request.POST)
            if quick_form.is_valid():
                task = quick_form.save(commit=False)
                task.owner = request.user
                task.task_list = active_list
                task.save()
                return back(task.pk if visible.filter(pk=task.pk).exists() else None)
        elif action in {"add-list", "rename-list"}:
            if action == "rename-list" and not active_list:
                raise Http404
            if action == "rename-list":
                form = ListForm(request.POST, instance=active_list, prefix="rename")
            else:
                form = NewFolderForm(request.POST, prefix="new-list")
            if form.is_valid():
                item = form.save(commit=False)
                item.owner = request.user
                try:
                    with transaction.atomic():
                        item.save()
                except IntegrityError:
                    form.add_error("name", "You already have a folder with this name.")
                else:
                    return redirect("task-list", list_id=item.pk)
            if action == "rename-list":
                active_list.refresh_from_db()
                rename_form = form
            else:
                add_list_form = form
        elif action == "delete-list" and active_list:
            active_list.delete()
            return redirect("all")
        elif action in {"edit", "rename-task", "move-task", "complete", "reopen", "delete", "add-step", "edit-step",
                        "complete-step", "reopen-step", "delete-step", "reschedule", "flag", "unflag", "save-notes"}:
            task = get_object_or_404(visible, pk=valid_id(request.POST.get("task_id")))
            target = owned.filter(pk=task.pk)
            if action == "delete":
                target.delete()
                return back(selected_id if selected_id != task.pk else None)
            if action in {"complete", "reopen"}:
                target.update(done=action == "complete")
                return back(selected_id)
            if action in {"flag", "unflag"}:
                target.update(flagged=action == "flag")
                return back(selected_id)
            if action == "reschedule":
                choice = request.POST.get("schedule")
                if choice == "custom":
                    try:
                        due = forms.DateField(required=False, input_formats=["%Y-%m-%d"]).clean(
                            request.POST.get("due"))
                    except forms.ValidationError:
                        return HttpResponseBadRequest("Invalid due date.")
                elif choice in quick_dates:
                    due = quick_dates[choice]
                else:
                    return HttpResponseBadRequest("Unknown date shortcut.")
                target.update(due=due)
                if selected_id == task.pk and today_only and due != today:
                    return back()
                return back(selected_id)
            selected = task
            if action == "edit":
                form = TaskForm(lists, request.POST, instance=task)
                if form.is_valid():
                    target.update(**form.cleaned_data)
                    return back(task.pk if visible.filter(pk=task.pk).exists() else None)
                selected = get_object_or_404(visible, pk=task.pk)
            elif action in {"rename-task", "move-task"}:
                if action == "rename-task":
                    title_form = form = TitleForm(request.POST, instance=task)
                else:
                    folder_form = form = FolderForm(lists, request.POST, instance=task)
                if form.is_valid():
                    target.update(**form.cleaned_data)
                    return back(task.pk if visible.filter(pk=task.pk).exists() else None)
                selected = get_object_or_404(visible, pk=task.pk)
            elif action == "save-notes":
                notes_form = NotesForm(request.POST, instance=task)
                if notes_form.is_valid():
                    target.update(notes=notes_form.cleaned_data["notes"])
                    return back(task.pk)
            elif action == "add-step":
                add_subtask_form = SubtaskForm(request.POST, prefix="new-step")
                if add_subtask_form.is_valid():
                    step = add_subtask_form.save(commit=False)
                    step.task = task
                    step.save()
                    return back(task.pk)
            else:
                step = get_object_or_404(task.subtasks, pk=valid_id(request.POST.get("step_id")))
                step_target = task.subtasks.filter(pk=step.pk)
                if action == "delete-step":
                    step_target.delete()
                    return back(task.pk)
                if action in {"complete-step", "reopen-step"}:
                    step_target.update(done=action == "complete-step")
                    return back(task.pk)
                subtask_form = SubtaskForm(request.POST, instance=step, prefix=f"step-{step.pk}")
                if subtask_form.is_valid():
                    step_target.update(**subtask_form.cleaned_data)
                    return back(task.pk)
                edited_subtask_id = step.pk
        else:
            return HttpResponseBadRequest("Unknown action.")

    steps = []
    if selected:
        if title_form is None:
            title_form = TitleForm(instance=selected)
        if folder_form is None:
            folder_form = FolderForm(lists, instance=selected)
        if notes_form is None:
            notes_form = NotesForm(instance=selected)
        steps = [(step, subtask_form if step.pk == edited_subtask_id else
                  SubtaskForm(instance=step, prefix=f"step-{step.pk}"))
                 for step in selected.subtasks.all()]

    ordered = list(visible.select_related("task_list").annotate(
        step_count=Count("subtasks"),
        done_steps=Count("subtasks", filter=Q(subtasks__done=True)),
    ).order_by("done", F("due").asc(nulls_last=True), "-pk"))
    return render(request, "tasks.html", {
        "view_url": view_url, "active_list": active_list, "today_only": today_only,
        "view_title": active_list.name if active_list else "Today" if today_only else "All tasks",
        "today": today, "quick_dates": quick_dates, "next_week": quick_dates["next-week"], "selected": selected, "groups": group_tasks(ordered, today),
        "lists": lists.annotate(open_count=Count("tasks", filter=Q(tasks__done=False, tasks__owner=request.user))),
        "all_count": owned.filter(done=False).count(),
        "today_count": owned.filter(due=today, done=False).count(),
        "view_count": sum(not task.done for task in ordered),
        "quick_form": quick_form, "title_form": title_form, "folder_form": folder_form, "notes_form": notes_form,
        "add_list_form": add_list_form, "rename_form": rename_form,
        "steps": steps, "done_steps": sum(step.done for step, _ in steps),
        "add_subtask_form": add_subtask_form,
    }, status=400 if request.method == "POST" else 200)


urlpatterns = [
    path("", tasks, name="all"),
    path("today/", tasks, {"today_only": True}, name="today"),
    path("lists/<int:list_id>/", tasks, name="task-list"),
    path("sign-in/", sign_in, name="sign-in"),
    path("sign-out/", sign_out, name="sign-out"),
]
