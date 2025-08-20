from django import forms

from .models import Plan
from .providers import codenames


class SubscriptionSelectionForm(forms.Form):
    plan = forms.ModelChoiceField(queryset=Plan.objects.filter(is_enabled=True))
    quantity = forms.IntegerField(min_value=1, initial=1)
    provider = forms.ChoiceField(choices=[(codename, codename) for codename in codenames])
