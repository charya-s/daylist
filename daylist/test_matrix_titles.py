from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import localdate

from .models import MatrixHeading, Task, TaskList


class MatrixTitleTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="matrixowner")
        self.other = get_user_model().objects.create_user(username="matrixother")
        self.folder = TaskList.objects.create(owner=self.owner, name="Work")
        self.task = Task.objects.create(owner=self.owner, title="Alpha task", task_list=self.folder, due=localdate(), flagged=True)
        self.client.force_login(self.owner)

    def rename(self, name, quadrant="do", **kwargs):
        return self.client.post(reverse("matrix"), {"action": "rename-quadrant", "quadrant": quadrant, f"quadrant-{quadrant}-name": name}, **kwargs)

    def test_names_are_saved_and_used_in_move_options_without_changing_tasks(self):
        response = self.rename("  My focus  ", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["quadrants"][0]["title"], "My focus")
        self.assertEqual(dict(response.context["matrix_choices"])["do"], "My focus")
        self.assertEqual(MatrixHeading.objects.get(owner=self.owner, quadrant="do").name, "My focus")
        self.task.refresh_from_db()
        self.assertEqual(self.task.matrix_quadrant, "")
        self.assertTrue(self.task.flagged)
        self.assertEqual(self.task.due, localdate())
        response = self.client.get(reverse("matrix"))
        self.assertEqual(response.context["quadrants"][0]["title"], "My focus")

    def test_each_account_has_its_own_names(self):
        private = MatrixHeading.objects.create(owner=self.other, quadrant="do", name="Private heading")
        response = self.client.get(reverse("matrix"))
        self.assertEqual(response.context["quadrants"][0]["title"], "Important & Urgent")
        self.rename("My heading")
        private.refresh_from_db()
        self.assertEqual(private.name, "Private heading")
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("matrix")).context["quadrants"][0]["title"], "Private heading")

    def test_invalid_names_and_quadrants_do_not_save(self):
        for name in ("", "   ", "x" * 81):
            response = self.rename(name)
            self.assertEqual(response.status_code, 400)
            self.assertTrue(response.context["quadrants"][0]["title_form"].errors)
        for quadrant in ("", "invalid", "99"):
            self.assertEqual(self.rename("Heading", quadrant).status_code, 400)
        self.assertFalse(MatrixHeading.objects.filter(owner=self.owner).exists())
        response = self.client.post(reverse("all"), {"action": "rename-quadrant", "quadrant": "do", "quadrant-do-name": "Wrong view"})
        self.assertEqual(response.status_code, 400)

    def test_rename_keeps_filters_and_the_selected_task(self):
        url = reverse("matrix") + f"?q=Alpha&folder={self.folder.pk}&task={self.task.pk}"
        response = self.client.post(url, {"action": "rename-quadrant", "quadrant": "do", "quadrant-do-name": "Focused work"}, follow=True)
        self.assertEqual(response.context["selected"].pk, self.task.pk)
        self.assertEqual(response.context["query"], "Alpha")
        self.assertEqual(response.context["matrix_folder"], str(self.folder.pk))
        self.assertEqual(dict(response.context["matrix_form"].fields["matrix_quadrant"].choices)["do"], "Focused work")

    def test_renaming_one_quadrant_does_not_rename_others(self):
        self.rename("Focus")
        self.rename("Plan", "schedule")
        titles = {quadrant["key"]: quadrant["title"] for quadrant in self.client.get(reverse("matrix")).context["quadrants"]}
        self.assertEqual(titles["do"], "Focus")
        self.assertEqual(titles["schedule"], "Plan")
        self.assertEqual(titles["batch"], "Not Important & Urgent")
