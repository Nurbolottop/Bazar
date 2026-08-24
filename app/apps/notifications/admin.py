from django.contrib import admin

from .models import Announcement, Notification, NotificationTemplate

admin.site.register(NotificationTemplate)
admin.site.register(Notification)
admin.site.register(Announcement)
