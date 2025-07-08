from django.http import HttpRequest, HttpResponse
from django.urls import include, path

from subscriptions.v0.api.views import ResourceHeadersMixin


class ResourceHeadersMixinTestView(ResourceHeadersMixin):
    def get(self, request: HttpRequest) -> HttpResponse:
        return HttpResponse("ok")


urlpatterns = [
    path("subscribe/", include("subscriptions.v0.urls")),
    path("api/", include("subscriptions.v0.api.urls")),
    path("api/headers_mixin/", ResourceHeadersMixinTestView.as_view()),
]
