from django.db import transaction
from django.utils.timezone import now

from .models import Task


@transaction.atomic
def trash_folder(folder):
    folder.deleted_at = now()
    folder.save(update_fields=["deleted_at"])
    Task.objects.filter(owner=folder.owner, task_list=folder).update(restore_folder=folder, task_list=None)


@transaction.atomic
def restore_folder(folder):
    folder.deleted_at = None
    folder.save(update_fields=["deleted_at"])
    Task.objects.filter(owner=folder.owner, restore_folder=folder, task_list__isnull=True).update(task_list=folder, restore_folder=None)
