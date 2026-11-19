from datetime import date
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import localdate, now
from .models import Task, TaskList, Subtask, TaskListTemplate


class FolderTemplateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username="templateowner")
        cls.other = get_user_model().objects.create_user(username="templateother")
        cls.folder = TaskList.objects.create(owner=cls.owner, name="Source", color="blue")
        cls.other_folder = TaskList.objects.create(owner=cls.other, name="Private folder")
        cls.task = Task.objects.create(owner=cls.owner, task_list=cls.folder, title="Prepare", notes="Instructions", flagged=True, due=date(2026, 11, 19), repeat="weekly", repeat_anchor=date(2026, 11, 19), position=1)
        cls.finished = Task.objects.create(owner=cls.owner, task_list=cls.folder, title="Already finished", done=True)
        Subtask.objects.create(task=cls.task, title="Check details", done=True, due=date(2026, 11, 18))
        Subtask.objects.create(task=cls.task, title="Trashed step", deleted_at=now())
        cls.private = TaskListTemplate.objects.create(owner=cls.other, name="Private template", tasks=[{"title": "Secret", "subtasks": []}])

    def setUp(self):
        self.client.force_login(self.owner)

    def payload(self, **changes):
        return {"action": "save-template", "name": "Project checklist", "color": "green", "items-TOTAL_FORMS": "2", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "1", "items-MAX_NUM_FORMS": "500", "items-0-title": "Prepare", "items-0-notes": "Instructions", "items-0-flagged": "on", "items-0-subtasks": "Check details\nSend update", "items-1-title": "Review", **changes}

    def create_template(self):
        response = self.client.post(reverse("new-template"), self.payload())
        self.assertEqual(response.status_code, 302)
        return TaskListTemplate.objects.get(owner=self.owner)

    def create_folder(self, preset, name="New project"):
        return self.client.post(reverse("all"), {"action": "add-list", "new-list-name": name, "new-list-color": preset.color, "new-list-template": preset.pk}, follow=True)

    def test_create_and_edit_template(self):
        item = self.create_template()
        self.assertEqual(item.tasks[0]["subtasks"], ["Check details", "Send update"])
        self.assertTrue(item.tasks[0]["flagged"])
        self.assertEqual(item.tasks[1]["notes"], "")
        response = self.client.post(reverse("edit-template", args=[item.pk]), self.payload(name="Updated checklist", **{"items-INITIAL_FORMS": "2", "items-1-title": "Final review"}))
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual((item.name, item.tasks[1]["title"]), ("Updated checklist", "Final review"))

    def test_folder_copy_is_independent_and_unchecked(self):
        preset = self.create_template()
        response = self.create_folder(preset)
        self.assertEqual(response.status_code, 200)
        folder = TaskList.objects.get(owner=self.owner, name="New project")
        copies = list(folder.tasks.order_by("position"))
        self.assertEqual([task.title for task in copies], ["Prepare", "Review"])
        self.assertEqual(copies[0].notes, "Instructions")
        self.assertTrue(copies[0].flagged)
        self.assertTrue(all(not task.done and task.due is None and not task.repeat and task.next_occurrence_id is None for task in copies))
        self.assertEqual(list(copies[0].subtasks.values_list("title", "done", "due")), [("Check details", False, None), ("Send update", False, None)])
        copies[0].title = "Changed copy"; copies[0].save()
        preset.refresh_from_db(); self.assertEqual(preset.tasks[0]["title"], "Prepare")
        preset.tasks[0]["title"] = "Changed preset"; preset.save()
        copies[0].refresh_from_db(); self.assertEqual(copies[0].title, "Changed copy")

    def test_save_existing_folder_preloads_open_tasks_and_active_subtasks(self):
        response = self.client.get(reverse("new-template"), {"folder": self.folder.pk})
        self.assertEqual(response.status_code, 200)
        initial = response.context["template_tasks"].initial
        self.assertEqual(len(initial), 1)
        self.assertEqual(initial[0]["title"], "Prepare")
        self.assertEqual(initial[0]["notes"], "Instructions")
        self.assertEqual(initial[0]["subtasks"], "Check details")
        self.assertNotIn("due", initial[0])

    def test_private_template_and_folder_cannot_be_used(self):
        self.assertEqual(self.client.get(reverse("edit-template", args=[self.private.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("edit-template", args=[self.private.pk]), self.payload()).status_code, 404)
        self.assertEqual(self.client.post(reverse("templates"), {"action": "delete-template", "template_id": self.private.pk}).status_code, 404)
        self.assertEqual(self.client.get(reverse("new-template"), {"folder": self.other_folder.pk}).status_code, 404)
        response = self.create_folder(self.private)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(TaskList.objects.filter(owner=self.owner, name="New project").exists())
        response = self.client.get(reverse("templates"))
        self.assertNotContains(response, "Private template")

    def test_empty_and_invalid_template_is_rejected(self):
        for changes in ({"items-0-title": "", "items-0-notes": "", "items-0-flagged": "", "items-0-subtasks": "", "items-1-title": ""}, {"items-0-subtasks": "x" * 201}, {"color": "invalid"}, {"items-TOTAL_FORMS": "100000"}):
            response = self.client.post(reverse("new-template"), self.payload(**changes))
            self.assertEqual(response.status_code, 400)
        self.assertFalse(TaskListTemplate.objects.filter(owner=self.owner).exists())

    def test_duplicate_template_name_and_deleted_item(self):
        preset = self.create_template()
        response = self.client.post(reverse("new-template"), self.payload())
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.context["template_form"].errors)
        response = self.client.post(reverse("edit-template", args=[preset.pk]), self.payload(**{"items-INITIAL_FORMS": "2", "items-1-DELETE": "on"}))
        self.assertEqual(response.status_code, 302)
        preset.refresh_from_db(); self.assertEqual(len(preset.tasks), 1)

    def test_deleting_template_keeps_created_folders(self):
        preset = self.create_template(); self.create_folder(preset)
        response = self.client.post(reverse("templates"), {"action": "delete-template", "template_id": preset.pk})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TaskListTemplate.objects.filter(pk=preset.pk).exists())
        self.assertEqual(TaskList.objects.get(owner=self.owner, name="New project").tasks.count(), 2)

    def create_task_template(self):
        data = self.payload(name="Weekly review", **{"items-TOTAL_FORMS": "1", "items-0-title": "Review progress"})
        response = self.client.post(reverse("new-template") + "?kind=task", data)
        self.assertEqual(response.status_code, 302)
        return TaskListTemplate.objects.get(owner=self.owner, kind=TaskListTemplate.Kind.TASK)

    def test_task_template_copies_one_task_to_the_current_view(self):
        preset = self.create_task_template()
        response = self.client.post(reverse("task-list", args=[self.folder.pk]), {"action": "add-task-template", "task_template": preset.pk}, follow=True)
        task = response.context["selected"]
        self.assertEqual(task.title, "Review progress")
        self.assertEqual(task.task_list_id, self.folder.pk)
        self.assertIsNone(task.due)
        self.assertFalse(task.done)
        self.assertTrue(task.flagged)
        self.assertEqual(task.notes, "Instructions")
        self.assertEqual(list(task.subtasks.values_list("title", "done")), [("Check details", False), ("Send update", False)])
        response = self.client.post(reverse("today"), {"action": "add-task-template", "task_template": preset.pk}, follow=True)
        self.assertEqual(response.context["selected"].due, localdate())
        self.assertIsNone(response.context["selected"].task_list_id)

    def test_task_template_requires_one_task_and_can_replace_it(self):
        response = self.client.post(reverse("new-template") + "?kind=task", self.payload())
        self.assertEqual(response.status_code, 400)
        self.assertFalse(TaskListTemplate.objects.filter(owner=self.owner).exists())
        preset = self.create_task_template()
        response = self.client.post(reverse("edit-template", args=[preset.pk]), self.payload(name="Weekly review", **{"items-INITIAL_FORMS": "1", "items-0-DELETE": "on", "items-1-title": "Replacement"}))
        self.assertEqual(response.status_code, 302)
        preset.refresh_from_db()
        self.assertEqual(preset.kind, "task")
        self.assertEqual([task["title"] for task in preset.tasks], ["Replacement"])

    def test_task_templates_and_folder_templates_cannot_be_mixed_or_accessed_by_others(self):
        preset = self.create_task_template()
        response = self.create_folder(preset)
        self.assertEqual(response.status_code, 400)
        response = self.client.get(reverse("templates"))
        self.assertEqual(list(response.context["task_templates"]), [preset])
        self.assertEqual(list(response.context["folder_templates"]), [])
        self.assertEqual(self.client.post(reverse("all"), {"action": "add-task-template", "task_template": self.private.pk}).status_code, 404)
        self.private.kind = "task"; self.private.save()
        self.assertEqual(self.client.post(reverse("all"), {"action": "add-task-template", "task_template": self.private.pk}).status_code, 404)
        self.assertEqual(self.client.get(reverse("new-template"), {"kind": "invalid"}).status_code, 400)

    def test_save_existing_task_as_task_template(self):
        response = self.client.get(reverse("new-template"), {"task": self.task.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["preset"].kind, "task")
        self.assertEqual(response.context["template_tasks"].initial[0]["subtasks"], "Check details")
        self.assertNotIn("due", response.context["template_tasks"].initial[0])
        foreign = Task.objects.create(owner=self.other, title="Private task")
        self.assertEqual(self.client.get(reverse("new-template"), {"task": foreign.pk}).status_code, 404)

    def test_matrix_labels_and_bottom_templates_link(self):
        response = self.client.get(reverse("matrix"))
        self.assertEqual([quadrant["title"] for quadrant in response.context["quadrants"]], ["Important & Urgent", "Important & Not Urgent", "Not Important & Urgent", "Not Important & Not Urgent"])
        self.assertNotContains(response, ">Do first<")
        html = response.content.decode()
        bottom = html[html.index('class="sidebar-bottom"'):]
        self.assertLess(bottom.index('href="/templates/"'), bottom.index('href="/completed/"'))



class ViewOrderTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="orderowner")
        self.client.force_login(self.owner)
        self.folder = TaskList.objects.create(owner=self.owner, name="Work")
        self.first = Task.objects.create(owner=self.owner, title="First", due=localdate(), task_list=self.folder, position=1)
        self.second = Task.objects.create(owner=self.owner, title="Second", due=localdate(), task_list=self.folder, position=2)
        self.undated = Task.objects.create(owner=self.owner, title="Undated")

    def test_all_today_and_folder_orders_are_independent(self):
        response = self.client.post(reverse("all"), {"action": "reorder-tasks", "order": [self.second.pk, self.first.pk, self.undated.pk]})
        self.assertEqual(response.status_code, 302)
        self.first.refresh_from_db(); self.second.refresh_from_db()
        self.assertEqual((self.first.position, self.second.position), (1, 2))
        self.assertEqual((self.first.all_position, self.second.all_position), (2, 1))
        response = self.client.post(reverse("today"), {"action": "reorder-tasks", "order": [self.first.pk, self.second.pk]})
        self.assertEqual(response.status_code, 302)
        self.first.refresh_from_db(); self.second.refresh_from_db()
        self.assertEqual((self.first.today_position, self.second.today_position), (1, 2))
        self.assertEqual((self.first.all_position, self.second.all_position), (2, 1))
        self.assertEqual(self.client.post(reverse("today"), {"action": "reorder-tasks", "order": [self.first.pk, self.undated.pk]}).status_code, 400)

    def test_all_has_empty_no_date_target_and_today_has_no_grips(self):
        self.undated.delete()
        response = self.client.get(reverse("all"))
        self.assertIn("No date", dict(response.context["groups"]))
        self.assertContains(response, "Drop a task here to remove its date.")
        response = self.client.get(reverse("today"))
        self.assertTrue(response.context["can_reorder"])
        self.assertNotContains(response, 'class="icon-button task-order-handle"')
        self.assertNotIn("No date", dict(response.context["groups"]))
