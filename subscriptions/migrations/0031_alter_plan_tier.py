# Generated by Django 3.2.12 on 2023-02-16 10:02

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0030_feature_is_negative'),
    ]

    operations = [
        migrations.AlterField(
            model_name='plan',
            name='tier',
            field=models.ForeignKey(blank=True, help_text='group of features connected to this plan', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='plans', to='subscriptions.tier'),
        ),
    ]
