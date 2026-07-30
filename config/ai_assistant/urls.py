from django.urls import path

from . import views


urlpatterns = [
    path('backoffice/', views.backoffice_assistant_settings, name='ai_assistant_backoffice'),
    path('context/', views.assistant_context, name='ai_assistant_context'),
    path('conversations/', views.create_conversation, name='ai_assistant_create_conversation'),
    path('conversations/<uuid:public_id>/messages/', views.conversation_message, name='ai_assistant_conversation_message'),
    path('tours/<slug:tour_key>/progress/', views.tour_progress, name='ai_assistant_tour_progress'),
    path('events/<int:event_id>/consume/', views.consume_event, name='ai_assistant_consume_event'),
    path('actions/<uuid:public_id>/confirm/', views.confirm_action, name='ai_assistant_confirm_action'),
    path('history/delete/', views.delete_history, name='ai_assistant_delete_history'),
]
