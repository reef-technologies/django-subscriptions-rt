from django import forms


class DummyForm(forms.Form):
    i_agree = forms.BooleanField(initial=False, required=True)
