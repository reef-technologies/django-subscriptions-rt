# Generated by Django 4.0.3 on 2022-07-28 19:29

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0015_auto_20220728_1920'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='subscriptionpayment',
            name='subscription',
        ),
        migrations.RemoveField(
            model_name='subscriptionpaymentrefund',
            name='original_payment',
        ),
        migrations.RemoveField(
            model_name='tax',
            name='subscription_payment',
        ),
    ]
