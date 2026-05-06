from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Invite, Connection, ConnectionRequest, Post, IntroChain, IntroStep, Message, TempChat, TempChatMessage, Notification

admin.site.register(User, UserAdmin)
admin.site.register(Invite)
admin.site.register(Connection)
admin.site.register(ConnectionRequest)
admin.site.register(Post)
admin.site.register(IntroChain)
admin.site.register(IntroStep)
admin.site.register(Message)
admin.site.register(TempChat)
admin.site.register(TempChatMessage)
admin.site.register(Notification)

