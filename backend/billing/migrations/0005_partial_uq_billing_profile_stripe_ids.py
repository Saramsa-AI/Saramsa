"""Replace global unique constraints on BillingProfile.stripe_customer_id
and stripe_subscription_id with partial uniques that only fire for
non-empty values. Without this, the second BillingProfile inserted with
empty default values would IntegrityError on the empty-string index.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0004_uq_billing_profile_org'),
    ]

    operations = [
        migrations.AlterField(
            model_name='billingprofile',
            name='stripe_customer_id',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AlterField(
            model_name='billingprofile',
            name='stripe_subscription_id',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AddConstraint(
            model_name='billingprofile',
            constraint=models.UniqueConstraint(
                condition=models.Q(('stripe_customer_id__gt', '')),
                fields=('stripe_customer_id',),
                name='uq_billing_profile_stripe_customer',
            ),
        ),
        migrations.AddConstraint(
            model_name='billingprofile',
            constraint=models.UniqueConstraint(
                condition=models.Q(('stripe_subscription_id__gt', '')),
                fields=('stripe_subscription_id',),
                name='uq_billing_profile_stripe_subscription',
            ),
        ),
    ]
