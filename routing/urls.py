from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('api/route/', views.RoutePlanView.as_view(), name='route-plan'),
    path('api/route/map/', views.route_map, name='route-map'),
]
