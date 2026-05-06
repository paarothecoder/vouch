from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('', views.landing, name='landing'),
    path('login/', views.login_view, name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('register/<uuid:token>/', views.register, name='register'),
    path('verify-email/', views.verify_email_view, name='verify_email'),
    path('resend-otp/', views.resend_otp_view, name='resend_otp'),
    path('ajax/send-prereg-otp/', views.send_prereg_otp, name='send_prereg_otp'),

    
    path('network/', views.network, name='network'),
    path('invites/', views.invites, name='invites'),
    path('invites/send/', views.send_invite, name='send_invite'),
    path('notifications/', views.notifications_view, name='notifications'),
    
    path('chat/', views.chat_list, name='chat_list'),
    path('chat/<str:username>/', views.chat_conversation, name='chat_conversation'),
    path('chat/<str:username>/connect/', views.chat_connect, name='chat_connect'),
    path('temp-chat/<int:chat_id>/', views.temp_chat_conversation, name='temp_chat_conversation'),
    path('temp-chat/<int:chat_id>/end/', views.end_temp_chat, name='end_temp_chat'),
    
    path('u/<str:username>/', views.profile, name='profile'),
    path('u/<str:username>/request-intro/', views.request_intro, name='request_intro'),
    path('u/<str:username>/request-temp-chat/', views.request_temp_chat, name='request_temp_chat'),
    path('u/<str:username>/disconnect/', views.remove_connection, name='remove_connection'),
    path('intro/<int:step_id>/<str:action>/', views.manage_intro, name='manage_intro'),
    path('explore/<str:username>/', views.explore, name='explore'),
]
