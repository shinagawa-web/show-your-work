import inspect
import json

import django
from django.conf import settings

settings.configure(
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "lab",
            "USER": "postgres",
            "PASSWORD": "test",
            "HOST": "127.0.0.1",
            "PORT": "15432",
        }
    }
)
django.setup()

from django.db import connection, models


class Account(models.Model):
    by_db_index = models.CharField(max_length=20, db_index=True)
    by_unique = models.CharField(max_length=20, unique=True)
    by_meta_indexes = models.CharField(max_length=20)

    class Meta:
        app_label = "lab"
        indexes = [models.Index(fields=["by_meta_indexes"], name="account_by_meta_indexes_idx")]


with connection.schema_editor(collect_sql=True) as editor:
    editor.create_model(Account)

print(json.dumps({
    "django": django.get_version(),
    "model": inspect.getsource(Account),
    "sql": [str(s) for s in editor.collected_sql],
}))
