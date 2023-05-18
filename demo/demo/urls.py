from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

from .views import ResourceHeadersMixinTestView


urlpatterns = [
    path('', TemplateView.as_view(template_name='demo/home.html'), name='home'),
    path('admin/', admin.site.urls),
    path('subscribe/', include('subscriptions.urls')),
    path('api/', include('subscriptions.api.urls')),
    path('api/headers_mixin/', ResourceHeadersMixinTestView.as_view()),
]
