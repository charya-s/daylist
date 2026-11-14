from django.db import migrations, models
from django.db.models import F


def initial_positions(apps, schema_editor):
    Task = apps.get_model("daylist", "Task")
    alias = schema_editor.connection.alias
    positions = {}
    batch = []
    for task in Task.objects.using(alias).order_by("done", F("due").asc(nulls_last=True), "-pk").iterator():
        key = (task.owner_id, task.task_list_id)
        positions[key] = positions.get(key, 0) + 1
        task.position = positions[key]
        batch.append(task)
        if len(batch) == 500:
            Task.objects.using(alias).bulk_update(batch, ["position"])
            batch = []
    if batch:
        Task.objects.using(alias).bulk_update(batch, ["position"])


class Migration(migrations.Migration):
    dependencies = [("daylist", "0007_trash")]
    operations = [
        migrations.AddField(model_name="task", name="position", field=models.PositiveIntegerField(default=0, db_index=True)),
        migrations.RunPython(initial_positions, migrations.RunPython.noop),
    ]
