#!/usr/bin/env python3
import django
from django.conf import settings

settings.configure(
    DATABASES={"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "lab", "USER": "postgres",
                           "PASSWORD": "test", "HOST": "127.0.0.1", "PORT": "15432"}},
    INSTALLED_APPS=[],
)
django.setup()

from django.db import connection, models


class Member(models.Model):
    code = models.CharField(max_length=20, db_index=True)
    shop_code = models.CharField(max_length=20)
    slug = models.CharField(max_length=20, unique=True)

    class Meta:
        app_label = "lab"
        indexes = [models.Index(fields=["shop_code"], name="lab_member_shop_code_idx")]


with connection.schema_editor(collect_sql=True) as editor:
    editor.create_model(Member)
for sql in editor.collected_sql:
    print(sql)
