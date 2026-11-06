import re
from datetime import timedelta
from urllib.parse import urlencode

from django import forms
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm, PasswordChangeForm
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.timezone import localdate, now
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from .models import Subtask, Task, TaskList
from .recurrence import complete_task
from .trash import trash_folder, restore_folder as restore_trashed_folder


class SignUpForm(UserCreationForm):
    username = forms.RegexField(
        regex=r"\A[A-Za-z0-9]+\Z", max_length=15, strip=False,
        help_text="15 characters or fewer. Letters and numbers only.",
        error_messages={"invalid": "Use only letters and numbers."},
        widget=forms.TextInput(attrs={"autocomplete": "username", "autocapitalize": "none",
                                      "autofocus": True, "pattern": "[A-Za-z0-9]+"}),
    )


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


class MatrixPlacementForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["matrix_quadrant"]
        labels = {"matrix_quadrant": "Matrix placement"}


class RepeatForm(forms.ModelForm):
    repeat_every = forms.IntegerField(min_value=1, max_value=365, initial=1, label="Every")

    class Meta:
        model = Task
        fields = ["repeat", "repeat_every"]
        labels = {"repeat": "Frequency", "repeat_every": "Every"}


class SubtaskForm(forms.ModelForm):
    class Meta:
        model = Subtask
        fields = ["title", "due"]
        widgets = {"due": DateInput(format="%Y-%m-%d")}


class SubtaskTitleForm(SubtaskForm):
    class Meta(SubtaskForm.Meta):
        fields = ["title"]


class FolderColorForm(forms.ModelForm):
    class Meta:
        model = TaskList
        fields = ["color"]
        widgets = {"color": forms.RadioSelect}


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


@never_cache
@require_http_methods(["GET", "POST"])
def sign_up(request):
    if request.user.is_authenticated:
        return redirect("all")
    form = SignUpForm(request.POST if request.method == "POST" else None)
    limited = False
    if request.method == "POST":
        # Share the cap across threads, including successful registrations.
        key = "sign-up-attempts"
        cache.add(key, 0, timeout=60)
        try:
            limited = cache.incr(key) > 10
        except ValueError:
            limited = True
        if not limited and form.is_valid():
            try:
                with transaction.atomic():
                    user = form.save()
            except IntegrityError:
                form.add_error("username", "This username is already taken.")
            else:
                login(request, user)
                return redirect("all")
    return render(request, "signup.html", {"form": form, "limited": limited},
                  status=429 if limited else 400 if request.method == "POST" else 200)


@require_POST
def sign_out(request):
    logout(request)
    return redirect("sign-in")



@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def change_password(request):
    form = PasswordChangeForm(request.user, request.POST if request.method == "POST" else None)
    form.fields["old_password"].label = "Current password"
    limited = False
    if request.method == "POST":
        key = f"password-change-attempts:{request.user.pk}"
        cache.add(key, 0, timeout=60)
        try:
            limited = cache.incr(key) > 10
        except ValueError:
            limited = True
        if not limited and form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            cache.delete(key)
            return redirect(reverse("change-password") + "?updated=1")
    return render(request, "change-password.html", {
        "form": form, "limited": limited, "updated": request.GET.get("updated") == "1",
    }, status=429 if limited else 400 if request.method == "POST" else 200)


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
def tasks(request, today_only=False, list_id=None, matrix=False, trash=False, completed=False):
    today = localdate()
    quick_dates = {
        "today": today, "tomorrow": today + timedelta(days=1),
        "next-week": today + timedelta(days=7), "no-date": None,
    }
    all_owned = Task.objects.filter(owner=request.user)
    owned = all_owned.filter(deleted_at__isnull=True)
    all_lists = TaskList.objects.filter(owner=request.user)
    lists = all_lists.filter(deleted_at__isnull=True)
    trashed_steps = Subtask.objects.filter(task__owner=request.user, task__deleted_at__isnull=True, deleted_at__isnull=False)
    trashed_folders = all_lists.filter(deleted_at__isnull=False)
    trash_count = all_owned.filter(deleted_at__isnull=False).count() + trashed_steps.count() + trashed_folders.count()
    active_list = get_object_or_404(lists, pk=list_id) if list_id else None
    query = request.GET.get("q", "").strip()[:200]
    matrix_folder = request.GET.get("folder", "") if matrix else ""
    scoped_list = active_list
    visible = all_owned.filter(deleted_at__isnull=False) if trash else owned.filter(done=completed)
    if matrix and matrix_folder:
        if matrix_folder == "none":
            visible = visible.filter(task_list__isnull=True)
        else:
            scoped_list = get_object_or_404(lists, pk=valid_id(matrix_folder))
            visible = visible.filter(task_list=scoped_list)
    if active_list:
        visible = visible.filter(task_list=active_list)
    elif today_only:
        visible = visible.filter(due=today)
    if query:
        matches = visible.filter(Q(title__icontains=query) | Q(notes__icontains=query) |
                                 Q(subtasks__title__icontains=query, subtasks__deleted_at__isnull=True) | Q(task_list__name__icontains=query))
        visible = visible.filter(pk__in=matches.values("pk"))
    filters = {}
    if query:
        filters["q"] = query
    if matrix_folder:
        filters["folder"] = matrix_folder
    view_path = request.path
    view_url = view_path + ("?" + urlencode(filters) if filters else "")
    clear_url = view_path + ("?" + urlencode({"folder": matrix_folder}) if matrix_folder else "")

    selected_id = valid_id(request.GET.get("task"))
    if trash and selected_id:
        raise Http404
    selected = get_object_or_404(visible, pk=selected_id) if selected_id else None
    quick_form = QuickTaskForm(initial={"due": today} if today_only else None)
    add_list_form = NewFolderForm(prefix="new-list")
    rename_form = ListForm(instance=active_list, prefix="rename") if active_list else None
    color_form = FolderColorForm(instance=active_list, prefix="folder-color") if active_list else None
    title_form = folder_form = None
    notes_form = repeat_form = matrix_form = None
    add_subtask_form = SubtaskForm(prefix="new-step")
    subtask_form = None
    edited_subtask_id = None

    def back(task_id=None):
        if task_id and not visible.filter(pk=task_id).exists():
            task_id = None
        params = {**filters, **({"task": task_id} if task_id else {})}
        return redirect(view_path + ("?" + urlencode(params) if params else ""))

    if request.method == "POST":
        action = request.POST.get("action")
        if trash:
            item_id = valid_id(request.POST.get("item_id"))
            if action in {"restore-task", "purge-task"}:
                item = get_object_or_404(all_owned.filter(deleted_at__isnull=False), pk=item_id)
                if action == "restore-task":
                    all_owned.filter(pk=item.pk).update(deleted_at=None)
                else:
                    item.delete()
            elif action in {"restore-step", "purge-step"}:
                item = get_object_or_404(trashed_steps, pk=item_id)
                if action == "restore-step":
                    Subtask.objects.filter(pk=item.pk).update(deleted_at=None)
                else:
                    item.delete()
            elif action in {"restore-folder", "purge-folder"}:
                item = get_object_or_404(trashed_folders, pk=item_id)
                if action == "restore-folder":
                    restore_trashed_folder(item)
                else:
                    item.delete()
            else:
                return HttpResponseBadRequest("Unknown Trash action.")
            return redirect(view_url)
        elif action == "add":
            quick_form = QuickTaskForm(request.POST)
            if quick_form.is_valid():
                task = quick_form.save(commit=False)
                task.owner = request.user
                task.task_list = scoped_list
                task.save()
                return back(task.pk if visible.filter(pk=task.pk).exists() else None)
        elif action == "color-list":
            if not active_list:
                raise Http404
            color_form = FolderColorForm(request.POST, instance=active_list, prefix="folder-color")
            if color_form.is_valid():
                color_form.save()
                return back(selected_id)
            active_list.refresh_from_db()
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
                    form.add_error("name", "You already have a folder with this name. Check Trash if it is not listed.")
                else:
                    return redirect("task-list", list_id=item.pk)
            if action == "rename-list":
                active_list.refresh_from_db()
                rename_form = form
            else:
                add_list_form = form
        elif action == "delete-list" and active_list:
            trash_folder(active_list)
            return redirect("all")
        elif action in {"edit", "rename-task", "move-task", "complete", "reopen", "delete", "add-step", "edit-step",
                        "complete-step", "reopen-step", "delete-step", "reschedule", "reschedule-step", "flag", "unflag", "save-notes", "save-repeat", "set-matrix"}:
            task = get_object_or_404(visible, pk=valid_id(request.POST.get("task_id")))
            target = owned.filter(pk=task.pk)
            if action == "delete":
                target.update(deleted_at=now())
                return back(selected_id if selected_id != task.pk else None)
            if action in {"complete", "reopen"}:
                if action == "complete":
                    try:
                        complete_task(task, today)
                    except forms.ValidationError as error:
                        task.refresh_from_db()
                        selected = task
                        repeat_form = RepeatForm({"repeat": task.repeat, "repeat_every": task.repeat_every}, instance=task)
                        repeat_form.is_valid()
                        repeat_form.add_error(None, error)
                    else:
                        return back(selected_id)
                else:
                    target.update(done=False)
                    return back(selected_id)
            if action in {"flag", "unflag"}:
                target.update(flagged=action == "flag")
                return back(selected_id)
            if action in {"reschedule", "reschedule-step"}:
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
                if action == "reschedule-step":
                    step = get_object_or_404(task.subtasks.filter(deleted_at__isnull=True), pk=valid_id(request.POST.get("step_id")))
                    task.subtasks.filter(pk=step.pk).update(due=due)
                    return back(selected_id or task.pk)
                changes = {"due": due, "repeat_anchor": due if task.repeat else None}
                if due is None:
                    changes.update(repeat="", repeat_every=1)
                target.update(**changes)
                if selected_id == task.pk and today_only and due != today:
                    return back()
                return back(selected_id)
            selected = task
            if action == "set-matrix":
                matrix_form = MatrixPlacementForm(request.POST, instance=task)
                if matrix_form.is_valid():
                    target.update(**matrix_form.cleaned_data)
                    return back(selected_id)
            elif action == "save-repeat":
                repeat_form = RepeatForm(request.POST, instance=task)
                if repeat_form.is_valid():
                    values = repeat_form.cleaned_data
                    due = (task.due or today) if values["repeat"] else task.due
                    target.update(**values, due=due, repeat_anchor=due if values["repeat"] else None)
                    return back(task.pk)
            elif action == "edit":
                form = TaskForm(lists, request.POST, instance=task)
                if form.is_valid():
                    changes = dict(form.cleaned_data)
                    changes["restore_folder"] = None
                    changes["repeat_anchor"] = changes["due"] if task.repeat else None
                    if changes["due"] is None:
                        changes.update(repeat="", repeat_every=1)
                    target.update(**changes)
                    return back(task.pk)
                selected = get_object_or_404(visible, pk=task.pk)
            elif action in {"rename-task", "move-task"}:
                if action == "rename-task":
                    title_form = form = TitleForm(request.POST, instance=task)
                else:
                    folder_form = form = FolderForm(lists, request.POST, instance=task)
                if form.is_valid():
                    values = dict(form.cleaned_data)
                    if action == "move-task":
                        values["restore_folder"] = None
                    target.update(**values)
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
            elif action in {"edit-step", "complete-step", "reopen-step", "delete-step"}:
                step = get_object_or_404(task.subtasks.filter(deleted_at__isnull=True), pk=valid_id(request.POST.get("step_id")))
                step_target = task.subtasks.filter(pk=step.pk)
                if action == "delete-step":
                    step_target.update(deleted_at=now())
                    return back(task.pk)
                if action in {"complete-step", "reopen-step"}:
                    step_target.update(done=action == "complete-step")
                    return back(task.pk)
                subtask_form = SubtaskTitleForm(request.POST, instance=step, prefix=f"step-{step.pk}")
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
        if repeat_form is None:
            repeat_form = RepeatForm(instance=selected)
        if matrix_form is None:
            matrix_form = MatrixPlacementForm(instance=selected)
        steps = [(step, subtask_form if step.pk == edited_subtask_id else
                  SubtaskTitleForm(instance=step, prefix=f"step-{step.pk}"))
                 for step in selected.subtasks.filter(deleted_at__isnull=True)]

    ordered = list(visible.select_related("task_list").annotate(
        step_count=Count("subtasks", filter=Q(subtasks__deleted_at__isnull=True)),
        done_steps=Count("subtasks", filter=Q(subtasks__done=True, subtasks__deleted_at__isnull=True)),
    ).order_by("done", F("due").asc(nulls_last=True), "-pk"))
    quadrants = [
        {"key": "do", "title": "Do first", "description": "Important & urgent", "tasks": []},
        {"key": "schedule", "title": "Schedule", "description": "Important & not urgent", "tasks": []},
        {"key": "batch", "title": "Delegate or batch", "description": "Less important & urgent", "tasks": []},
        {"key": "reconsider", "title": "Reconsider", "description": "Less important & not urgent", "tasks": []},
    ]
    if matrix:
        for task in ordered:
            if not task.done:
                urgent = task.due is not None and task.due <= today
                index = (0 if task.flagged else 2) + (0 if urgent else 1)
                chosen = next((quadrant for quadrant in quadrants if quadrant["key"] == task.matrix_quadrant), quadrants[index])
                chosen["tasks"].append(task)
    if trash and query:
        trashed_folders = trashed_folders.filter(name__icontains=query)
        trashed_steps = trashed_steps.filter(Q(title__icontains=query) | Q(task__title__icontains=query))
    trash_tasks = sorted(ordered, key=lambda task: task.deleted_at, reverse=True) if trash else []
    trash_folders = list(trashed_folders.order_by("-deleted_at")) if trash else []
    trash_steps = list(trashed_steps.select_related("task").order_by("-deleted_at")) if trash else []
    return render(request, "tasks.html", {
        "view_url": view_url, "view_path": view_path, "task_separator": "&" if filters else "?",
        "query": query, "clear_url": clear_url, "matrix": matrix, "trash": trash, "completed": completed,
        "completed_count": owned.filter(done=True).count(),
        "trash_count": trash_count, "trash_tasks": trash_tasks, "trash_folders": trash_folders,
        "trash_steps": trash_steps, "trash_items_count": len(trash_tasks) + len(trash_folders) + len(trash_steps), "matrix_folder": matrix_folder,
        "quadrants": quadrants, "matrix_choices": Task.MatrixQuadrant.choices, "matrix_form": matrix_form, "result_count": sum(not task.done for task in ordered) if matrix else len(ordered),
        "active_list": active_list, "today_only": today_only,
        "view_title": "Trash" if trash else "Completed" if completed else "Matrix" if matrix else active_list.name if active_list else "Today" if today_only else "All tasks",
        "today": today, "quick_dates": quick_dates, "next_week": quick_dates["next-week"], "selected": selected, "groups": group_tasks(ordered, today),
        "lists": lists.annotate(open_count=Count("tasks", filter=Q(tasks__done=False, tasks__owner=request.user, tasks__deleted_at__isnull=True))),
        "all_count": owned.filter(done=False).count(),
        "today_count": owned.filter(due=today, done=False).count(),
        "view_count": sum(not task.done for task in ordered),
        "quick_form": quick_form, "title_form": title_form, "folder_form": folder_form, "notes_form": notes_form,
        "add_list_form": add_list_form, "rename_form": rename_form, "repeat_form": repeat_form, "color_form": color_form,
        "steps": steps, "done_steps": sum(step.done for step, _ in steps),
        "add_subtask_form": add_subtask_form,
    }, status=400 if request.method == "POST" else 200)


urlpatterns = [
    path("", tasks, name="all"),
    path("today/", tasks, {"today_only": True}, name="today"),
    path("matrix/", tasks, {"matrix": True}, name="matrix"),
    path("completed/", tasks, {"completed": True}, name="completed"),
    path("trash/", tasks, {"trash": True}, name="trash"),
    path("account/password/", change_password, name="change-password"),
    path("lists/<int:list_id>/", tasks, name="task-list"),
    path("sign-in/", sign_in, name="sign-in"),
    path("sign-up/", sign_up, name="sign-up"),
    path("sign-out/", sign_out, name="sign-out"),
]
