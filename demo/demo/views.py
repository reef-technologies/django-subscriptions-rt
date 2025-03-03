from django.http import HttpResponse

from subscriptions.api.views import ResourceHeadersMixin


class ResourceHeadersMixinTestView(ResourceHeadersMixin):
    def get(self, request):
        return HttpResponse("ok")
