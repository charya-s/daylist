from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.timezone import now

from .models import Subtask, Task, TaskList
from .recurrence import complete_task
from .trash import trash_folder


class ManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username="owner")
        cls.other = get_user_model().objects.create_user(username="other")
        cls.folder = TaskList.objects.create(owner=cls.owner, name="Research", color="blue")
        cls.destination = TaskList.objects.create(owner=cls.owner, name="Other folder")
        cls.task = Task.objects.create(owner=cls.owner, title="My task", task_list=cls.folder, notes="Keep notes", due=date(2026, 11, 5))
        cls.step = Subtask.objects.create(task=cls.task, title="My step")

    def setUp(self):
        self.client.force_login(self.owner)

    def act(self, action, **values):
        return self.client.post(reverse("all"), {"action": action, "task_id": self.task.pk, **values})

    def trash_action(self, action, item_id, **kwargs):
        return self.client.post(reverse("trash"), {"action": action, "item_id": item_id}, **kwargs)

    def test_change_folder_color_without_changing_its_name_or_tasks(self):
        response = self.client.post(reverse("task-list", args=[self.folder.pk]), {"action": "color-list", "folder-color-color": "purple"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.folder.refresh_from_db()
        self.assertEqual((self.folder.name, self.folder.color), ("Research", "purple"))
        self.task.refresh_from_db()
        self.assertEqual(self.task.task_list_id, self.folder.pk)
        self.assertContains(response, 'data-color="purple"')

    def test_invalid_color_does_not_save(self):
        response = self.client.post(reverse("task-list", args=[self.folder.pk]), {"action": "color-list", "folder-color-color": "invalid"})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.context["color_form"].errors)
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.color, "blue")

    @patch("daylist.views.localdate", return_value=date(2026, 11, 5))
    def test_subtask_date_shortcuts_custom_date_and_clear(self, today):
        for schedule, due in (("today", date(2026, 11, 5)), ("tomorrow", date(2026, 11, 6)), ("next-week", date(2026, 11, 12)), ("custom", date(2026, 12, 20)), ("no-date", None)):
            with self.subTest(schedule=schedule):
                response = self.act("reschedule-step", step_id=self.step.pk, schedule=schedule, due="2026-12-20")
                self.assertEqual(response.status_code, 302)
                self.step.refresh_from_db()
                self.assertEqual(self.step.due, due)
        self.task.refresh_from_db()
        self.assertEqual(self.task.due, date(2026, 11, 5))

    def test_subtask_date_errors_and_foreign_ids(self):
        response = self.act("reschedule-step", step_id=self.step.pk, schedule="custom", due="2026-02-30")
        self.assertEqual(response.status_code, 400)
        other_task = Task.objects.create(owner=self.other, title="Private")
        foreign_step = Subtask.objects.create(task=other_task, title="Private step")
        self.assertEqual(self.act("reschedule-step", step_id=foreign_step.pk, schedule="today").status_code, 404)
        self.step.refresh_from_db()
        self.assertIsNone(self.step.due)

    def test_editing_subtask_title_keeps_its_due_date(self):
        self.step.due = date(2026, 12, 1)
        self.step.save()
        response = self.act("edit-step", step_id=self.step.pk, **{f"step-{self.step.pk}-title": "Renamed"})
        self.assertEqual(response.status_code, 302)
        self.step.refresh_from_db()
        self.assertEqual((self.step.title, self.step.due), ("Renamed", date(2026, 12, 1)))

    def test_task_trash_hides_task_from_views_search_and_counts(self):
        self.act("delete")
        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.deleted_at)
        for url in (reverse("all"), reverse("today"), reverse("matrix")):
            response = self.client.get(url, {"q": "My task"})
            self.assertEqual(response.context["view_count"], 0)
            self.assertEqual(response.context["groups"], [])
        response = self.client.get(reverse("trash"))
        self.assertContains(response, "My task")
        self.assertEqual(response.context["trash_count"], 1)
        self.assertTrue(Subtask.objects.filter(pk=self.step.pk).exists())
        self.assertEqual(self.act("complete").status_code, 404)

    def test_restore_task_preserves_notes_subtasks_and_folder(self):
        self.act("delete")
        response = self.trash_action("restore-task", self.task.pk)
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.deleted_at)
        self.assertEqual((self.task.notes, self.task.task_list_id), ("Keep notes", self.folder.pk))
        self.assertTrue(self.task.subtasks.filter(pk=self.step.pk, deleted_at__isnull=True).exists())

    def test_subtask_trash_is_excluded_from_search_badges_and_recurrence(self):
        self.act("delete-step", step_id=self.step.pk)
        self.step.refresh_from_db()
        self.assertIsNotNone(self.step.deleted_at)
        response = self.client.get(reverse("all"), {"q": "My step"})
        self.assertEqual(response.context["result_count"], 0)
        response = self.client.get(reverse("all"))
        listed = [item for _, group in response.context["groups"] for item in group]
        self.assertEqual(listed[0].step_count, 0)
        self.task.repeat = "daily"
        self.task.save()
        complete_task(self.task, date(2026, 11, 5))
        self.task.refresh_from_db()
        self.assertFalse(self.task.next_occurrence.subtasks.exists())
        self.trash_action("restore-step", self.step.pk)
        self.step.refresh_from_db()
        self.assertIsNone(self.step.deleted_at)

    def test_trashing_parent_does_not_restore_previously_trashed_subtasks(self):
        self.act("delete-step", step_id=self.step.pk)
        self.act("delete")
        response = self.client.get(reverse("trash"))
        self.assertEqual(len(response.context["trash_tasks"]), 1)
        self.assertEqual(len(response.context["trash_steps"]), 0)
        self.trash_action("restore-task", self.task.pk)
        self.step.refresh_from_db()
        self.assertIsNotNone(self.step.deleted_at)
        response = self.client.get(reverse("trash"))
        self.assertEqual(len(response.context["trash_steps"]), 1)

    def test_folder_trash_leaves_tasks_in_all_and_restore_reconnects(self):
        response = self.client.post(reverse("task-list", args=[self.folder.pk]), {"action": "delete-list"})
        self.assertEqual(response.status_code, 302)
        self.folder.refresh_from_db()
        self.task.refresh_from_db()
        self.assertIsNotNone(self.folder.deleted_at)
        self.assertIsNone(self.task.task_list_id)
        self.assertEqual(self.task.restore_folder_id, self.folder.pk)
        self.assertIsNone(self.task.deleted_at)
        self.assertEqual(self.client.get(reverse("task-list", args=[self.folder.pk])).status_code, 404)
        response = self.client.get(reverse("all"))
        self.assertContains(response, "My task")
        self.assertEqual(response.context["all_count"], 1)
        self.trash_action("restore-folder", self.folder.pk)
        self.folder.refresh_from_db()
        self.task.refresh_from_db()
        self.assertIsNone(self.folder.deleted_at)
        self.assertEqual(self.task.task_list_id, self.folder.pk)
        self.assertIsNone(self.task.restore_folder_id)

    def test_folder_restore_does_not_move_tasks_the_user_moved_elsewhere(self):
        trash_folder(self.folder)
        response = self.act("move-task", task_list=self.destination.pk)
        self.assertEqual(response.status_code, 302)
        self.trash_action("restore-folder", self.folder.pk)
        self.task.refresh_from_db()
        self.assertEqual(self.task.task_list_id, self.destination.pk)
        self.assertIsNone(self.task.restore_folder_id)

    def test_explicit_no_folder_prevents_reattachment_on_restore(self):
        trash_folder(self.folder)
        self.act("move-task", task_list="")
        self.trash_action("restore-folder", self.folder.pk)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.task_list_id)

    def test_permanent_task_deletion_removes_subtasks(self):
        self.act("delete")
        self.trash_action("purge-task", self.task.pk)
        self.assertFalse(Task.objects.filter(pk=self.task.pk).exists())
        self.assertFalse(Subtask.objects.filter(pk=self.step.pk).exists())

    def test_permanent_folder_deletion_keeps_tasks(self):
        trash_folder(self.folder)
        self.trash_action("purge-folder", self.folder.pk)
        self.assertFalse(TaskList.objects.filter(pk=self.folder.pk).exists())
        self.task.refresh_from_db()
        self.assertIsNone(self.task.task_list_id)
        self.assertIsNone(self.task.restore_folder_id)

    def test_only_trashed_items_can_be_permanently_deleted(self):
        self.assertEqual(self.trash_action("purge-task", self.task.pk).status_code, 404)
        self.assertEqual(self.trash_action("purge-folder", self.folder.pk).status_code, 404)
        self.assertTrue(Task.objects.filter(pk=self.task.pk).exists())

    def test_trash_search_covers_folders_and_subtasks(self):
        trash_folder(self.folder)
        self.act("delete-step", step_id=self.step.pk)
        response = self.client.get(reverse("trash"), {"q": "Research"})
        self.assertEqual(response.context["trash_items_count"], 1)
        self.assertContains(response, "Research")
        response = self.client.get(reverse("trash"), {"q": "My step"})
        self.assertEqual(response.context["trash_items_count"], 1)
        self.assertContains(response, "My step")

    def test_trash_and_color_actions_are_private(self):
        folder = TaskList.objects.create(owner=self.other, name="Private folder", deleted_at=now())
        task = Task.objects.create(owner=self.other, title="Private task", deleted_at=now())
        parent = Task.objects.create(owner=self.other, title="Private parent")
        step = Subtask.objects.create(task=parent, title="Private step", deleted_at=now())
        response = self.client.get(reverse("trash"))
        self.assertNotContains(response, "Private")
        for action, item in (("restore-task", task), ("purge-task", task), ("restore-folder", folder), ("purge-folder", folder), ("restore-step", step), ("purge-step", step)):
            with self.subTest(action=action):
                self.assertEqual(self.trash_action(action, item.pk).status_code, 404)
        self.assertEqual(self.client.post(reverse("task-list", args=[folder.pk]), {"action": "color-list", "folder-color-color": "red"}).status_code, 404)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PasswordChangeTests(TestCase):
    password = "Cedar!Moon-River-8472"
    replacement = "A different password 925"

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="owner", password=self.password)
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def change(self, **values):
        return self.client.post(reverse("change-password"), {"old_password": self.password, "new_password1": self.replacement, "new_password2": self.replacement, **values})

    def test_requires_sign_in_and_csrf(self):
        anonymous = Client()
        self.assertRedirects(anonymous.get(reverse("change-password")), reverse("sign-in") + "?next=/account/password/")
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse("change-password"), {}).status_code, 403)

    def test_current_password_and_new_password_rules_are_enforced(self):
        for values in ({"old_password": "wrong"}, {"new_password2": "Mismatch12345678"},
                       {"new_password1": "short1", "new_password2": "short1"},
                       {"new_password1": "Long password without digits", "new_password2": "Long password without digits"}):
            with self.subTest(values=values):
                self.assertEqual(self.change(**values).status_code, 400)
                self.user.refresh_from_db()
                self.assertTrue(self.user.check_password(self.password))

    def test_change_keeps_current_session_and_invalidates_other_sessions(self):
        second = Client()
        second.login(username="owner", password=self.password)
        previous_key = self.client.session.session_key
        response = self.change()
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.replacement))
        self.assertFalse(self.user.check_password(self.password))
        self.assertNotEqual(self.client.session.session_key, previous_key)
        self.assertEqual(self.client.get(reverse("all")).status_code, 200)
        self.assertEqual(second.get(reverse("all")).status_code, 302)
        response = self.client.get(response.url)
        self.assertContains(response, "Password changed")
        self.assertFalse(Client().login(username="owner", password=self.password))
        self.assertTrue(Client().login(username="owner", password=self.replacement))

    def test_password_change_attempts_are_limited_per_user(self):
        for _ in range(10):
            self.assertEqual(self.change(old_password="wrong").status_code, 400)
        self.assertEqual(self.change().status_code, 429)
