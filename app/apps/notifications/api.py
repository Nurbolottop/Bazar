"""API ленты уведомлений (ТЗ-02 п. 6.2)."""
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api.pagination import DefaultPagination

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'code', 'title', 'body', 'payload', 'created_at', 'read_at']


class MyNotificationsView(APIView):
    """GET /api/v1/me/notifications — лента уведомлений, включая объявления."""

    def get(self, request):
        queryset = Notification.objects.filter(tenant=request.user).order_by('-created_at')
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request)
        return paginator.get_paginated_response(
            NotificationSerializer(page, many=True).data)


class MarkReadView(APIView):
    """POST /api/v1/me/notifications/read — отметка уведомлений прочитанными."""

    def post(self, request):
        ids = request.data.get('ids', [])
        if not isinstance(ids, list):
            ids = []
        updated = Notification.objects.filter(
            tenant=request.user, pk__in=[i for i in ids if isinstance(i, int)],
            read_at__isnull=True,
        ).update(read_at=timezone.now())
        return Response({'updated': updated})
