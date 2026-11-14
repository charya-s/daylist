from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import now

from .models import Subtask, Task, TaskList
from .planner import month_dates


@patch("daylist.views.localdate", return_value=date(2026, 11, 14))
class CalendarAndOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username="planner")
        cls.other = get_user_model().objects.create_user(username="privateplanner")
        cls.folder = TaskList.objects.create(owner=cls.owner, name="Research", color="blue")
        cls.second = TaskList.objects.create(owner=cls.owner, name="Personal")
        cls.private_folder = TaskList.objects.create(owner=cls.other, name="Hidden")
        cls.first = Task.objects.create(owner=cls.owner, title="First task", task_list=cls.folder, due=date(2026, 11, 3), position=1)
        cls.later = Task.objects.create(owner=cls.owner, title="Later task", task_list=cls.folder, due=date(2026, 11, 24), position=2, flagged=True)
        cls.undated = Task.objects.create(owner=cls.owner, title="Undated task", task_list=cls.folder, position=3)
        cls.personal = Task.objects.create(owner=cls.owner, title="Personal task", task_list=cls.second, due=date(2026, 11, 3))
        cls.unfiled = Task.objects.create(owner=cls.owner, title="Unfiled task", due=date(2026, 11, 4))
        cls.finished = Task.objects.create(owner=cls.owner, title="Finished task", task_list=cls.folder, due=date(2026, 11, 5), done=True)
        cls.trashed = Task.objects.create(owner=cls.owner, title="Trashed task", task_list=cls.folder, due=date(2026, 11, 3), deleted_at=now())
        cls.private = Task.objects.create(owner=cls.other, title="Private task", task_list=cls.private_folder, due=date(2026, 11, 3))
        cls.next_month = Task.objects.create(owner=cls.owner, title="December task", due=date(2026, 12, 1))
        cls.step = Subtask.objects.create(task=cls.first, title="Step")

    def setUp(self):
        self.client.force_login(self.owner)

    def event_ids(self, response):
        return {task.pk for week in response.context["calendar_weeks"] for day in week for task in day["tasks"]}

    def reorder(self, ids, url=None):
        return self.client.post(url or reverse("task-list", args=[self.folder.pk]), {"action": "reorder-tasks", "order": ids})

    def test_calendar_defaults_to_current_month_and_all_owned_deadlines(self, _):
        response = self.client.get(reverse("calendar"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["month_title"], "November 2026")
        self.assertEqual(self.event_ids(response), {self.first.pk, self.later.pk, self.personal.pk, self.unfiled.pk, self.finished.pk})
        self.assertNotContains(response, 'id="quick-title"')
        self.assertEqual(response.context["matrix_folder"], "")

    def test_folder_and_no_folder_filters(self, _):
        response = self.client.get(reverse("calendar"), {"folder": self.folder.pk})
        self.assertEqual(self.event_ids(response), {self.first.pk, self.later.pk, self.finished.pk})
        response = self.client.get(reverse("calendar"), {"folder": "none"})
        self.assertEqual(self.event_ids(response), {self.unfiled.pk})
        self.assertEqual(self.client.get(reverse("calendar"), {"folder": self.private_folder.pk}).status_code, 404)

    def test_search_and_month_navigation_preserve_filters(self, _):
        response = self.client.get(reverse("calendar"), {"month": "2026-11", "folder": self.folder.pk, "q": "Later"})
        self.assertEqual(self.event_ids(response), {self.later.pk})
        self.assertIn("month=2026-12", response.context["next_month_url"])
        self.assertIn("folder=" + str(self.folder.pk), response.context["next_month_url"])
        self.assertIn("q=Later", response.context["next_month_url"])
        self.assertNotIn("q=", response.context["clear_url"])
        self.assertIn("month=2026-11", response.context["clear_url"])
        self.assertEqual(self.event_ids(self.client.get(reverse("calendar"), {"month": "2026-12"})), {self.next_month.pk})

    def test_calendar_selection_and_delete_are_scoped(self, _):
        response = self.client.get(reverse("calendar"), {"task": self.first.pk})
        self.assertEqual(response.context["selected"].pk, self.first.pk)
        for task in (self.private, self.undated, self.trashed, self.next_month):
            self.assertEqual(self.client.get(reverse("calendar"), {"task": task.pk}).status_code, 404)
        response = self.client.post(reverse("calendar") + "?month=2026-11", {"action": "delete", "task_id": self.first.pk}, follow=True)
        self.assertNotIn(self.first.pk, self.event_ids(response))
        self.first.refresh_from_db()
        self.assertIsNotNone(self.first.deleted_at)
        self.assertTrue(Subtask.objects.filter(pk=self.step.pk).exists())

    def test_invalid_month_and_year_boundaries(self, _):
        for month in ("2026-13", "0000-01", "2026-0", "bad"):
            self.assertEqual(self.client.get(reverse("calendar"), {"month": month}).status_code, 400)
        first = self.client.get(reverse("calendar"), {"month": "0001-01"})
        last = self.client.get(reverse("calendar"), {"month": "9999-12"})
        self.assertIsNone(first.context["previous_month_url"])
        self.assertIsNone(last.context["next_month_url"])

    def test_saved_folder_order_overrides_due_dates(self, _):
        response = self.reorder([self.undated.pk, self.later.pk, self.first.pk])
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse("task-list", args=[self.folder.pk]))
        self.assertEqual([task.pk for task in response.context["folder_tasks"]], [self.undated.pk, self.later.pk, self.first.pk])
        self.assertNotContains(response, 'class="task-group')
        self.first.refresh_from_db(); self.later.refresh_from_db()
        self.assertEqual(self.first.due, date(2026, 11, 3))
        self.assertTrue(self.later.flagged)
        self.assertEqual(self.later.task_list_id, self.folder.pk)
        all_view = self.client.get(reverse("all"))
        self.assertTrue(all_view.context["groups"])

    def test_invalid_or_stale_order_cannot_change_tasks(self, _):
        before = list(Task.objects.values_list("pk", "position"))
        for ids in ([self.first.pk], [self.first.pk, self.first.pk, self.undated.pk],
                    [self.first.pk, self.private.pk, self.undated.pk], [self.first.pk, self.finished.pk, self.undated.pk],
                    [self.first.pk, self.personal.pk, self.undated.pk], [self.first.pk, self.trashed.pk, self.undated.pk], ["bad"]):
            self.assertEqual(self.reorder(ids).status_code, 400)
        self.assertEqual(list(Task.objects.values_list("pk", "position")), before)
        self.assertEqual(self.reorder([self.first.pk], reverse("all")).status_code, 400)
        self.assertEqual(self.reorder([self.first.pk], reverse("task-list", args=[self.private_folder.pk])).status_code, 404)
        self.assertEqual(self.reorder([self.first.pk], reverse("task-list", args=[self.folder.pk]) + "?q=First").status_code, 400)

    def test_search_disables_drag_but_preserves_manual_order(self, _):
        self.reorder([self.undated.pk, self.later.pk, self.first.pk])
        response = self.client.get(reverse("task-list", args=[self.folder.pk]), {"q": "task"})
        self.assertFalse(response.context["can_reorder"])
        self.assertEqual([task.pk for task in response.context["folder_tasks"]], [self.undated.pk, self.later.pk, self.first.pk])

    def test_visible_trash_buttons_for_rows_details_and_folder(self, _):
        response = self.client.get(reverse("task-list", args=[self.folder.pk]), {"task": self.first.pk})
        for title in (self.first.title, self.folder.name, self.step.title):
            self.assertContains(response, 'aria-label="Move ' + title + ' to Trash"')
        self.assertNotContains(response, 'class="step-edit"')

    def test_moving_task_to_another_folder_starts_it_at_top(self, _):
        self.client.post(reverse("all"), {"action": "move-task", "task_id": self.later.pk, "task_list": self.second.pk})
        self.later.refresh_from_db()
        self.assertEqual(self.later.position, 0)
        self.assertEqual(self.later.task_list_id, self.second.pk)
