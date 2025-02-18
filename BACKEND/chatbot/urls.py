from django.urls import path
from . import views

urlpatterns = [
    path('call-model/', views.chatbot_api, name='chatbot_api'),
    path('', views.call_model, name='call_model')
]
