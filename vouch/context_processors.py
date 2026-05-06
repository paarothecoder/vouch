from .models import Notification

def notification_count(request):
    """Make unread notification count available in all templates."""
    if request.user.is_authenticated:
        count = Notification.objects.filter(user=request.user, is_read=False).count()
        return {'unread_notif_count': count}
    return {'unread_notif_count': 0}
