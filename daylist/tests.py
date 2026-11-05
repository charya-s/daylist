from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Subtask, Task, TaskList


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AccountTests(TestCase):
    password = "Cedar!Moon-River-8472"

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def register(self, username="newuser", **extra):
        return self.client.post(reverse("sign-up"), {
            "username": username, "password1": self.password,
            "password2": self.password, **extra,
        })

    def test_login_links_to_signup_and_back(self):
        response = self.client.get(reverse("sign-in"))
        self.assertContains(response, 'href="/sign-up/"')
        response = self.client.get(reverse("sign-up"))
        self.assertContains(response, 'href="/sign-in/"')
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_signup_creates_regular_user_and_signs_in(self):
        response = self.register(is_staff="true", is_superuser="true")
        self.assertRedirects(response, reverse("all"))
        user = get_user_model().objects.get(username="newuser")
        self.assertTrue(user.check_password(self.password))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertFalse(Task.objects.filter(owner=user).exists())
        self.assertFalse(TaskList.objects.filter(owner=user).exists())

    def test_existing_account_can_sign_in_and_sign_out(self):
        user = get_user_model().objects.create_user(username="existing", password=self.password)
        response = self.client.post(reverse("sign-in"), {"username": user.username, "password": self.password})
        self.assertRedirects(response, reverse("all"))
        response = self.client.post(reverse("sign-out"))
        self.assertRedirects(response, reverse("sign-in"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_duplicate_usernames_including_different_case_are_rejected(self):
        get_user_model().objects.create_user(username="existing", password=self.password)
        for username in ("existing", "EXISTING"):
            with self.subTest(username=username):
                response = self.register(username)
                self.assertEqual(response.status_code, 400)
                self.assertIn("username", response.context["form"].errors)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_weak_and_mismatched_passwords_are_rejected(self):
        for values in ({"password1": "short", "password2": "short"},
                       {"password2": "A different password!"}):
            with self.subTest(values=values):
                response = self.register(**values)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.context["form"].errors)
        self.assertFalse(get_user_model().objects.exists())

    def test_password_guidelines_show_only_length_and_number(self):
        response = self.client.get(reverse("sign-up"))
        self.assertContains(response, "Your password must contain at least 14 characters.")
        self.assertContains(response, "Your password must contain at least one number.")
        self.assertNotContains(response, "personal information")
        self.assertNotContains(response, "commonly used password")
        self.assertNotContains(response, "entirely numeric")

    def test_password_requires_both_length_and_number(self):
        for password in ("abcdefghijkl1", "abcdefghijklmno"):
            # A 13-character password with a number, or a long password without one.
            with self.subTest(password=password):
                response = self.register(password1=password, password2=password)
                self.assertEqual(response.status_code, 400)
                self.assertIn("password2", response.context["form"].errors)
        self.assertFalse(get_user_model().objects.exists())

    def test_password_accepts_exact_boundary_and_no_extra_rules(self):
        for username, password in (("boundary", "abcdefghijklm1"),
                                   ("numeric", "12345678901234"),
                                   ("sameaspassword", "sameaspassword1")):
            with self.subTest(username=username):
                self.client.logout()
                response = self.register(username, password1=password, password2=password)
                self.assertRedirects(response, reverse("all"))
                self.assertTrue(get_user_model().objects.get(username=username).check_password(password))

    def test_username_guidelines_and_browser_constraints(self):
        response = self.client.get(reverse("sign-up"))
        self.assertContains(response, "15 characters or fewer. Letters and numbers only.")
        self.assertContains(response, 'maxlength="15"')
        self.assertContains(response, 'pattern="[A-Za-z0-9]+"')
        self.assertNotContains(response, "150 characters")

    def test_username_rejects_long_names_and_non_alphanumeric_characters(self):
        for username in ("abcdefghijklmn12", "user-name", "user_name", "user.name",
                         "user@name", "user+name", "user name", "user!", "user ", "café", ""):
            with self.subTest(username=username):
                cache.clear()
                response = self.register(username)
                self.assertEqual(response.status_code, 400)
                self.assertIn("username", response.context["form"].errors)
        self.assertFalse(get_user_model().objects.exists())

    def test_username_accepts_letters_numbers_and_fifteen_character_boundary(self):
        for username in ("A", "123456", "Alice123", "Abcdefghijklm12"):
            with self.subTest(username=username):
                self.client.logout()
                response = self.register(username)
                self.assertRedirects(response, reverse("all"))
                self.assertTrue(get_user_model().objects.filter(username=username).exists())

    def test_existing_long_username_can_still_sign_in(self):
        username = "legacy-user-with-a-long-name"
        get_user_model().objects.create_user(username=username, password=self.password)
        response = self.client.post(reverse("sign-in"), {"username": username, "password": self.password})
        self.assertRedirects(response, reverse("all"))

    def test_signup_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse("sign-up"), {"username": "newuser", "password1": self.password, "password2": self.password})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(get_user_model().objects.exists())

    def test_signed_in_user_cannot_create_another_account(self):
        self.register()
        response = self.register("anotheruser")
        self.assertRedirects(response, reverse("all"))
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_signup_attempt_limit(self):
        for _ in range(10):
            self.assertEqual(self.register(password1="short", password2="short").status_code, 400)
        response = self.register()
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many attempts", status_code=429)
        self.assertFalse(get_user_model().objects.exists())
        # Sign-up throttling does not prevent an existing user from signing in.
        get_user_model().objects.create_user(username="existing", password=self.password)
        response = self.client.post(reverse("sign-in"), {"username": "existing", "password": self.password})
        self.assertRedirects(response, reverse("all"))


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AccountPrivacyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username="owner")
        cls.visitor = get_user_model().objects.create_user(username="visitor")
        cls.folder = TaskList.objects.create(owner=cls.owner, name="Private folder")
        cls.task = Task.objects.create(owner=cls.owner, title="Private task", notes="Private note", task_list=cls.folder)
        cls.step = Subtask.objects.create(task=cls.task, title="Private step")
        cls.own_task = Task.objects.create(owner=cls.visitor, title="My task")

    def setUp(self):
        self.client.force_login(self.visitor)

    def test_account_cannot_read_another_accounts_workspace(self):
        response = self.client.get(reverse("all"))
        self.assertContains(response, "My task")
        self.assertNotContains(response, "Private task")
        self.assertNotContains(response, "Private folder")
        self.assertEqual(self.client.get(reverse("task-list", args=[self.folder.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("all"), {"task": self.task.pk}).status_code, 404)

    def test_account_cannot_modify_another_accounts_task(self):
        for action in ("rename-task", "save-notes", "complete", "delete", "reschedule", "flag", "add-step"):
            with self.subTest(action=action):
                response = self.client.post(reverse("all"), {"action": action, "task_id": self.task.pk, "title": "Changed", "notes": "Changed"})
                self.assertEqual(response.status_code, 404)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "Private task")
        self.assertEqual(self.task.notes, "Private note")
        self.assertFalse(self.task.done)
        self.assertFalse(self.task.flagged)

    def test_account_cannot_move_tasks_to_another_accounts_folder(self):
        response = self.client.post(reverse("all"), {"action": "move-task", "task_id": self.own_task.pk, "task_list": self.folder.pk})
        self.assertEqual(response.status_code, 400)
        self.own_task.refresh_from_db()
        self.assertIsNone(self.own_task.task_list_id)

    def test_account_cannot_attach_another_accounts_subtask_to_own_task(self):
        response = self.client.post(reverse("all"), {"action": "delete-step", "task_id": self.own_task.pk, "step_id": self.step.pk})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Subtask.objects.filter(pk=self.step.pk).exists())
