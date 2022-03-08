from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView


urlpatterns = [
    path('', TemplateView.as_view(template_name='demo/home.html'), name='home'),
    path('admin/', admin.site.urls),
    path('subscribe/', include('payments.urls')),
]
