from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import localdate, now

from .models import Task, TaskList


class CompletedViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username="completedowner")
        cls.other = get_user_model().objects.create_user(username="completedother")
        cls.folder = TaskList.objects.create(owner=cls.owner, name="Folder")
        cls.open_task = Task.objects.create(owner=cls.owner, title="Still open", task_list=cls.folder, due=localdate())
        cls.finished = Task.objects.create(owner=cls.owner, title="Finished work", task_list=cls.folder, done=True, due=localdate())
        cls.trashed = Task.objects.create(owner=cls.owner, title="Trashed work", done=True, deleted_at=now())
        cls.private = Task.objects.create(owner=cls.other, title="Private work", done=True)

    def setUp(self):
        self.client.force_login(self.owner)

    def test_open_views_exclude_completed_tasks(self):
        for url in (reverse("all"), reverse("today"), reverse("task-list", args=[self.folder.pk])):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual([task.pk for _, tasks in response.context["groups"] for task in tasks], [self.open_task.pk])
                self.assertEqual(response.context["completed_count"], 1)

    def test_completed_view_has_only_owned_active_finished_tasks(self):
        response = self.client.get(reverse("completed"))
        self.assertEqual([task.pk for _, tasks in response.context["groups"] for task in tasks], [self.finished.pk])
        self.assertNotContains(response, 'id="quick-title"')
        for task in (self.open_task, self.trashed, self.private):
            self.assertEqual(self.client.get(reverse("completed"), {"task": task.pk}).status_code, 404)

    def test_complete_and_reopen_moves_between_views_without_losing_folder(self):
        response = self.client.post(reverse("task-list", args=[self.folder.pk]) + f"?task={self.open_task.pk}", {"action": "complete", "task_id": self.open_task.pk}, follow=True)
        self.assertIsNone(response.context["selected"])
        self.open_task.refresh_from_db()
        self.assertTrue(self.open_task.done)
        response = self.client.post(reverse("completed") + f"?task={self.open_task.pk}", {"action": "reopen", "task_id": self.open_task.pk}, follow=True)
        self.assertIsNone(response.context["selected"])
        self.open_task.refresh_from_db()
        self.assertFalse(self.open_task.done)
        self.assertEqual(self.open_task.task_list_id, self.folder.pk)

    def test_completed_search_and_trash(self):
        response = self.client.get(reverse("completed"), {"q": "no match"})
        self.assertEqual(response.context["groups"], [])
        response = self.client.post(reverse("completed"), {"action": "delete", "task_id": self.finished.pk}, follow=True)
        self.assertEqual(response.context["completed_count"], 0)
        self.finished.refresh_from_db()
        self.assertIsNotNone(self.finished.deleted_at)
