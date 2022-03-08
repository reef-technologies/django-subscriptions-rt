from typing import Union

from django.http import HttpResponse, JsonResponse
from django.views.generic import DetailView, ListView, View

from .models import Plan


class PlanListView(ListView):
    queryset = Plan.objects.all()
    context_object_name = 'plans'


class PlanView(DetailView):
    model = Plan

    def get_object(self):
        return self.model.objects.get(slug=self.kwargs['plan_slug'])


class PaymentMixin(View):
    pass
