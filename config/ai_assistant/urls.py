from django.urls import path

from . import views


urlpatterns = [
    path('backoffice/', views.backoffice_assistant_settings, name='ai_assistant_backoffice'),
    path('context/', views.assistant_context, name='ai_assistant_context'),
    path('verification/account-status/request/', views.request_account_status_code, name='ai_assistant_request_status_code'),
    path('verification/account-status/verify/', views.verify_account_status_code, name='ai_assistant_verify_status_code'),
    path('access/login-failure/', views.record_login_failure, name='ai_assistant_login_failure'),
    path('conversations/', views.create_conversation, name='ai_assistant_create_conversation'),
    path('conversations/<uuid:public_id>/messages/', views.conversation_message, name='ai_assistant_conversation_message'),
    path('tours/<slug:tour_key>/progress/', views.tour_progress, name='ai_assistant_tour_progress'),
    path('events/<int:event_id>/consume/', views.consume_event, name='ai_assistant_consume_event'),
    path('actions/<uuid:public_id>/confirm/', views.confirm_action, name='ai_assistant_confirm_action'),
    path('history/delete/', views.delete_history, name='ai_assistant_delete_history'),
]
