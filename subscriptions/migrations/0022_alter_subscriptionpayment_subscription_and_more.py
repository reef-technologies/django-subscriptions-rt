# Generated by Django 4.0.3 on 2022-08-01 08:25

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0021_alter_subscriptionpayment_subscription_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='subscriptionpayment',
            name='subscription',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='payments', to='subscriptions.subscription'),
        ),
        migrations.AlterField(
            model_name='subscriptionpayment',
            name='uid',
            field=models.UUIDField(primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name='subscriptionpaymentrefund',
            name='original_payment',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='refunds', to='subscriptions.subscriptionpayment'),
        ),
        migrations.AlterField(
            model_name='subscriptionpaymentrefund',
            name='uid',
            field=models.UUIDField(primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name='tax',
            name='subscription_payment',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='taxes', to='subscriptions.subscriptionpayment'),
        ),
    ]
