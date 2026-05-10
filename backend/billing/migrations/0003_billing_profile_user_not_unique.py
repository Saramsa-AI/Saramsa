from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0002_org_scoped_quota"),
    ]

    operations = [
        migrations.AlterField(
            model_name="billingprofile",
            name="user_id",
            field=models.CharField(db_index=True, max_length=64),
        ),
    ]
