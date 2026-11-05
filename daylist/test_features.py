from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .models import Subtask, Task, TaskList
from .recurrence import complete_task, next_due_date


class MatrixAndSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username="owner")
        cls.other = get_user_model().objects.create_user(username="other")
        cls.folder = TaskList.objects.create(owner=cls.owner, name="Research")
        cls.private_folder = TaskList.objects.create(owner=cls.other, name="Private")
        Task.objects.create(owner=cls.other, title="Private needle", task_list=cls.private_folder)

    def setUp(self):
        self.client.force_login(self.owner)

    @patch("daylist.views.localdate", return_value=date(2026, 11, 5))
    def test_matrix_classifies_existing_tasks_and_omits_completed(self, today):
        cases = [("do", True, date(2026, 11, 4)), ("schedule", True, None),
                 ("batch", False, date(2026, 11, 5)), ("reconsider", False, date(2026, 11, 6))]
        expected = {}
        for quadrant, flagged, due in cases:
            expected[quadrant] = Task.objects.create(owner=self.owner, title=quadrant, flagged=flagged, due=due).pk
        Task.objects.create(owner=self.owner, title="Already done", flagged=True, due=date(2026, 11, 4), done=True)
        response = self.client.get(reverse("matrix"))
        self.assertEqual(response.status_code, 200)
        for quadrant in response.context["quadrants"]:
            self.assertEqual([task.pk for task in quadrant["tasks"]], [expected[quadrant["key"]]])
        self.assertEqual(response.context["view_count"], 4)
        self.assertNotContains(response, "Private needle")

    def test_matrix_folder_filter_and_ownership(self):
        included = Task.objects.create(owner=self.owner, title="Included", task_list=self.folder)
        Task.objects.create(owner=self.owner, title="Unfiled")
        response = self.client.get(reverse("matrix"), {"folder": self.folder.pk})
        cards = [task.pk for quadrant in response.context["quadrants"] for task in quadrant["tasks"]]
        self.assertEqual(cards, [included.pk])
        response = self.client.get(reverse("matrix"), {"folder": "none"})
        self.assertContains(response, "Unfiled")
        self.assertNotContains(response, "Included")
        self.assertEqual(self.client.get(reverse("matrix"), {"folder": self.private_folder.pk}).status_code, 404)

    @patch("daylist.views.localdate", return_value=date(2026, 11, 5))
    def test_manual_matrix_move_preserves_date_and_flag(self, today):
        task = Task.objects.create(owner=self.owner, title="Placed manually", due=date(2026, 11, 20), flagged=False)
        response = self.client.post(reverse("matrix"), {"action": "set-matrix", "task_id": task.pk, "matrix_quadrant": "do"}, follow=True)
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.matrix_quadrant, "do")
        self.assertEqual(task.due, date(2026, 11, 20))
        self.assertFalse(task.flagged)
        do = next(quadrant for quadrant in response.context["quadrants"] if quadrant["key"] == "do")
        self.assertEqual([item.pk for item in do["tasks"]], [task.pk])
        # Later changes to dates and flags do not override an explicit placement.
        self.client.post(reverse("matrix"), {"action": "flag", "task_id": task.pk})
        self.client.post(reverse("matrix"), {"action": "reschedule", "task_id": task.pk, "schedule": "no-date"})
        response = self.client.get(reverse("matrix"))
        do = next(quadrant for quadrant in response.context["quadrants"] if quadrant["key"] == "do")
        self.assertEqual([item.pk for item in do["tasks"]], [task.pk])

    def test_automatic_placement_can_be_restored(self):
        task = Task.objects.create(owner=self.owner, title="Automatic again", matrix_quadrant="do")
        response = self.client.post(reverse("matrix"), {"action": "set-matrix", "task_id": task.pk, "matrix_quadrant": ""}, follow=True)
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.matrix_quadrant, "")
        quadrant = next(item for item in response.context["quadrants"] if item["key"] == "reconsider")
        self.assertEqual([item.pk for item in quadrant["tasks"]], [task.pk])

    def test_invalid_or_foreign_manual_move_is_rejected(self):
        task = Task.objects.create(owner=self.owner, title="My task")
        response = self.client.post(reverse("matrix"), {"action": "set-matrix", "task_id": task.pk, "matrix_quadrant": "invalid"})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.context["matrix_form"].errors)
        task.refresh_from_db()
        self.assertEqual(task.matrix_quadrant, "")
        foreign = Task.objects.get(owner=self.other)
        response = self.client.post(reverse("matrix"), {"action": "set-matrix", "task_id": foreign.pk, "matrix_quadrant": "do"})
        self.assertEqual(response.status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.matrix_quadrant, "")

    def test_search_matches_title_notes_subtasks_and_folder(self):
        title = Task.objects.create(owner=self.owner, title="Needle in title")
        notes = Task.objects.create(owner=self.owner, title="Notes match", notes="a NEEDLE here")
        steps = Task.objects.create(owner=self.owner, title="Subtask match")
        Subtask.objects.create(task=steps, title="needle one", done=True)
        Subtask.objects.create(task=steps, title="needle two")
        Subtask.objects.create(task=steps, title="third step")
        Task.objects.create(owner=self.owner, title="Unrelated")
        response = self.client.get(reverse("all"), {"q": "needle"})
        found = [task for _, group in response.context["groups"] for task in group]
        self.assertEqual({task.pk for task in found}, {title.pk, notes.pk, steps.pk})
        self.assertEqual(len(found), 3)
        matching = next(task for task in found if task.pk == steps.pk)
        self.assertEqual((matching.step_count, matching.done_steps), (3, 1))
        self.assertNotContains(response, "Private needle")
        filed = Task.objects.create(owner=self.owner, title="Folder match", task_list=self.folder)
        response = self.client.get(reverse("all"), {"q": "research"})
        self.assertEqual(response.context["result_count"], 1)
        self.assertContains(response, filed.title)

    def test_search_stays_in_current_folder(self):
        Task.objects.create(owner=self.owner, title="needle", task_list=self.folder)
        Task.objects.create(owner=self.owner, title="other needle")
        response = self.client.get(reverse("task-list", args=[self.folder.pk]), {"q": "needle"})
        self.assertEqual(response.context["result_count"], 1)
        self.assertNotContains(response, "other needle")

    def test_search_parameters_survive_selection_and_save(self):
        task = Task.objects.create(owner=self.owner, title="A needle", task_list=self.folder)
        url = reverse("matrix") + f"?q=needle&folder={self.folder.pk}"
        response = self.client.get(url)
        self.assertContains(response, f'q=needle&amp;folder={self.folder.pk}&amp;task={task.pk}')
        response = self.client.post(url + f"&task={task.pk}", {"action": "save-notes", "task_id": task.pk, "notes": "Saved"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["query"], "needle")
        self.assertEqual(response.context["matrix_folder"], str(self.folder.pk))
        task.refresh_from_db()
        self.assertEqual(task.notes, "Saved")

    def test_edit_that_removes_search_match_does_not_redirect_to_missing_task(self):
        task = Task.objects.create(owner=self.owner, title="Needle")
        response = self.client.post(reverse("all") + f"?q=needle&task={task.pk}",
                                    {"action": "rename-task", "task_id": task.pk, "title": "Changed"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected"])
        self.assertContains(response, "No matching tasks")


class RepeatDateTests(SimpleTestCase):
    def next_date(self, repeat, due, today, every=1, anchor=None):
        return next_due_date(Task(repeat=repeat, due=due, repeat_every=every, repeat_anchor=anchor), today)

    def test_daily_and_weekly_skip_missed_occurrences(self):
        self.assertEqual(self.next_date("daily", date(2026, 1, 1), date(2026, 1, 10), 3), date(2026, 1, 13))
        self.assertEqual(self.next_date("weekly", date(2026, 1, 1), date(2026, 1, 17), 2), date(2026, 1, 29))

    def test_early_completion_keeps_the_scheduled_cadence(self):
        self.assertEqual(self.next_date("daily", date(2026, 1, 20), date(2026, 1, 10)), date(2026, 1, 21))

    def test_month_end_recovers_original_day(self):
        anchor = date(2026, 1, 31)
        self.assertEqual(self.next_date("monthly", anchor, anchor), date(2026, 2, 28))
        self.assertEqual(self.next_date("monthly", date(2026, 2, 28), date(2026, 2, 28), anchor=anchor), date(2026, 3, 31))
        self.assertEqual(self.next_date("monthly", anchor, date(2026, 3, 30)), date(2026, 3, 31))

    def test_yearly_leap_day_recovers_in_leap_year(self):
        self.assertEqual(self.next_date("yearly", date(2027, 2, 28), date(2027, 2, 28), anchor=date(2024, 2, 29)), date(2028, 2, 29))


class RecurringTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username="owner")
        cls.other = get_user_model().objects.create_user(username="other")
        cls.folder = TaskList.objects.create(owner=cls.owner, name="Work")

    def setUp(self):
        self.client.force_login(self.owner)

    def task(self, **values):
        defaults = {"title": "Recurring", "due": date(2026, 1, 31), "repeat": "monthly",
                    "repeat_anchor": date(2026, 1, 31), "owner": self.owner,
                    "task_list": self.folder, "notes": "Keep notes", "flagged": True}
        return Task.objects.create(**{**defaults, **values})

    def test_completion_preserves_history_and_copies_fresh_subtasks(self):
        task = self.task(matrix_quadrant="schedule")
        Subtask.objects.create(task=task, title="First", due=date(2026, 1, 30), done=True)
        Subtask.objects.create(task=task, title="Second")
        complete_task(task, date(2026, 1, 31))
        task.refresh_from_db()
        self.assertTrue(task.done)
        following = task.next_occurrence
        self.assertIsNotNone(following)
        self.assertEqual(following.due, date(2026, 2, 28))
        self.assertEqual((following.owner_id, following.task_list_id, following.notes, following.flagged),
                         (task.owner_id, task.task_list_id, task.notes, task.flagged))
        self.assertFalse(following.done)
        self.assertEqual(following.matrix_quadrant, "schedule")
        self.assertEqual(following.repeat_anchor, date(2026, 1, 31))
        self.assertEqual(list(following.subtasks.values_list("title", "due", "done")),
                         [("First", date(2026, 2, 27), False), ("Second", None, False)])
        self.assertTrue(task.subtasks.get(title="First").done)

    def test_repeated_completion_and_reopening_do_not_duplicate_next_task(self):
        task = self.task()
        complete_task(task, date(2026, 1, 31))
        complete_task(task, date(2026, 1, 31))
        Task.objects.filter(pk=task.pk).update(done=False)
        complete_task(task, date(2026, 1, 31))
        self.assertEqual(Task.objects.count(), 2)

    def test_monthly_successor_keeps_anchor(self):
        task = self.task()
        complete_task(task, date(2026, 1, 31))
        task.refresh_from_db()
        following = task.next_occurrence
        complete_task(following, date(2026, 2, 28))
        following.refresh_from_db()
        self.assertEqual(following.next_occurrence.due, date(2026, 3, 31))

    def test_non_recurring_completion_does_not_create_a_copy(self):
        task = self.task(repeat="")
        complete_task(task, date(2026, 1, 31))
        self.assertEqual(Task.objects.count(), 1)
        task.refresh_from_db()
        self.assertTrue(task.done)

    @patch("daylist.views.localdate", return_value=date(2026, 11, 5))
    def test_enabling_repeat_on_undated_task_starts_today(self, today):
        task = self.task(repeat="", due=None, repeat_anchor=None)
        response = self.client.post(reverse("all") + f"?task={task.pk}",
                                    {"action": "save-repeat", "task_id": task.pk, "repeat": "weekly", "repeat_every": 2}, follow=True)
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual((task.due, task.repeat, task.repeat_every), (date(2026, 11, 5), "weekly", 2))
        self.assertEqual(Task.objects.count(), 1)

    def test_invalid_repeat_rules_do_not_save(self):
        task = self.task(repeat="")
        for repeat, every in (("daily", 0), ("daily", 366), ("hourly", 1)):
            with self.subTest(repeat=repeat, every=every):
                response = self.client.post(reverse("all"), {"action": "save-repeat", "task_id": task.pk, "repeat": repeat, "repeat_every": every})
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.context["repeat_form"].errors)
                task.refresh_from_db()
                self.assertEqual(task.repeat, "")

    def test_clearing_due_date_stops_repetition(self):
        task = self.task()
        response = self.client.post(reverse("all"), {"action": "reschedule", "task_id": task.pk, "schedule": "no-date"})
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertIsNone(task.due)
        self.assertEqual(task.repeat, "")
        self.assertIsNone(task.repeat_anchor)

    def test_rescheduling_resets_repeat_anchor(self):
        task = self.task()
        self.client.post(reverse("all"), {"action": "reschedule", "task_id": task.pk, "schedule": "custom", "due": "2026-02-15"})
        task.refresh_from_db()
        self.assertEqual(task.repeat_anchor, date(2026, 2, 15))
        complete_task(task, date(2026, 2, 15))
        task.refresh_from_db()
        self.assertEqual(task.next_occurrence.due, date(2026, 3, 15))

    def test_out_of_range_repeat_date_rolls_back_completion(self):
        task = self.task(due=date(9999, 12, 31), repeat="daily")
        response = self.client.post(reverse("all"), {"action": "complete", "task_id": task.pk})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Choose an earlier due date", status_code=400)
        task.refresh_from_db()
        self.assertFalse(task.done)
        self.assertEqual(Task.objects.count(), 1)

    @patch("daylist.views.localdate", return_value=date(2026, 11, 5))
    def test_completing_recurring_matrix_card_schedules_the_successor(self, today):
        task = self.task(due=date(2026, 11, 5), repeat="daily", repeat_anchor=date(2026, 11, 5))
        response = self.client.post(reverse("matrix") + f"?task={task.pk}",
                                    {"action": "complete", "task_id": task.pk}, follow=True)
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertTrue(task.done)
        self.assertEqual(task.next_occurrence.due, date(2026, 11, 6))
        quadrants = {quadrant["key"]: quadrant["tasks"] for quadrant in response.context["quadrants"]}
        self.assertEqual(quadrants["do"], [])
        self.assertEqual([item.pk for item in quadrants["schedule"]], [task.next_occurrence_id])
        self.assertEqual(response.context["view_count"], 1)

    def test_cannot_change_another_accounts_recurrence(self):
        task = self.task(owner=self.other, task_list=None)
        response = self.client.post(reverse("all"), {"action": "save-repeat", "task_id": task.pk, "repeat": "daily", "repeat_every": 1})
        self.assertEqual(response.status_code, 404)
        task.refresh_from_db()
        self.assertEqual(task.repeat, "monthly")
