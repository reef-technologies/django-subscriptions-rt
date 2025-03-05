from django.http import HttpResponse

from subscriptions.v0 import ResourceHeadersMixin


class ResourceHeadersMixinTestView(ResourceHeadersMixin):
    def get(self, request):
        return HttpResponse("ok")
