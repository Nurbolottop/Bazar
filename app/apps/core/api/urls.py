"""Маршруты REST API /api/v1/ (ТЗ-02 п. 6.2)."""
from django.urls import path

from apps.accounts.api import DeviceRegisterView, LoginView, LogoutView
from apps.billing.api import MyChargeDetailView, MyChargesView
from apps.notifications.api import MarkReadView, MyNotificationsView
from apps.payments.api import (
    ClaimCreateView, ClaimWithdrawView, HistoryDetailView, HistoryView, ReceiptView,
)
from apps.tenants.api import (
    MySpotDetailView, MySpotsView, ProfileView, SettingsView, SummaryView,
)

from .views import AppConfigView, PaymentInfoView

urlpatterns = [
    path('auth/login', LoginView.as_view()),
    path('auth/logout', LogoutView.as_view()),
    path('me', ProfileView.as_view()),
    path('me/summary', SummaryView.as_view()),
    path('me/spots', MySpotsView.as_view()),
    path('me/spots/<int:pk>', MySpotDetailView.as_view()),
    path('me/charges', MyChargesView.as_view()),
    path('me/charges/<int:pk>', MyChargeDetailView.as_view()),
    path('me/payments', HistoryView.as_view()),
    path('me/payments/<str:item_id>', HistoryDetailView.as_view()),
    path('me/receipts/<int:claim_id>', ReceiptView.as_view()),
    path('me/notifications', MyNotificationsView.as_view()),
    path('me/notifications/read', MarkReadView.as_view()),
    path('me/devices', DeviceRegisterView.as_view()),
    path('me/settings', SettingsView.as_view()),
    path('payment-info', PaymentInfoView.as_view()),
    path('payment-claims', ClaimCreateView.as_view()),
    path('payment-claims/<int:pk>/withdraw', ClaimWithdrawView.as_view()),
    path('app/config', AppConfigView.as_view()),
]
