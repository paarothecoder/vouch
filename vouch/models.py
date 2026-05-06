from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
import uuid
import datetime

ACCOUNT_TYPE_CHOICES = [
    ('professional', 'Professional'),
    ('student', 'Student'),
]

class User(AbstractUser):
    bio = models.TextField(blank=True, null=True, max_length=500)
    title = models.CharField(max_length=100, blank=True, null=True)
    company = models.CharField(max_length=100, blank=True, null=True)
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES, default='professional')
    is_email_verified = models.BooleanField(default=False)

    def __str__(self):
        return self.username

def default_invite_expiry():
    return timezone.now() + datetime.timedelta(minutes=60)

class Invite(models.Model):
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_invites')
    email = models.EmailField()
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    status = models.CharField(max_length=20, choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('expired', 'Expired')], default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_invite_expiry)

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"{self.email} by {self.sender.username}"

class Connection(models.Model):
    user1 = models.ForeignKey(User, on_delete=models.CASCADE, related_name='connections_initiated')
    user2 = models.ForeignKey(User, on_delete=models.CASCADE, related_name='connections_received')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user1', 'user2')
    
    def __str__(self):
        return f"{self.user1.username} <-> {self.user2.username}"

class ConnectionRequest(models.Model):
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_requests')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_requests')
    introducer = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='introduced_requests')
    status = models.CharField(max_length=20, choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('rejected', 'Rejected')], default='pending')
    message = models.TextField(blank=True, null=True, max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('sender', 'receiver')

    def __str__(self):
        return f"{self.sender.username} -> {self.receiver.username}"

class Post(models.Model):
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='posts')
    content = models.TextField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Post by {self.author.username} on {self.created_at.strftime('%Y-%m-%d %H:%M')}"

class IntroChain(models.Model):
    initiator = models.ForeignKey(User, on_delete=models.CASCADE, related_name='initiated_chains')
    target = models.ForeignKey(User, on_delete=models.CASCADE, related_name='targeted_chains')
    message = models.TextField(blank=True, null=True, max_length=500)
    status = models.CharField(max_length=20, choices=[
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('rejected', 'Rejected'),
    ], default='in_progress')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Chain: {self.initiator.username} -> {self.target.username} ({self.status})"

    def current_step(self):
        return self.steps.filter(status='pending').order_by('order').first()

class IntroStep(models.Model):
    chain = models.ForeignKey(IntroChain, on_delete=models.CASCADE, related_name='steps')
    from_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='intro_from')
    to_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='intro_to')
    next_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='intro_next',
                                   help_text="The person this step is asking to_user to forward to")
    order = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=[
        ('waiting', 'Waiting'),
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
    ], default='waiting')
    is_final = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"Step {self.order}: {self.from_user.username} asks {self.to_user.username} to intro {self.next_user.username}"

class Message(models.Model):
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_messages')
    content = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.sender.username} → {self.receiver.username}: {self.content[:50]}"

class TempChat(models.Model):
    initiator = models.ForeignKey(User, on_delete=models.CASCADE, related_name='initiated_temp_chats')
    target = models.ForeignKey(User, on_delete=models.CASCADE, related_name='targeted_temp_chats')
    message = models.TextField(blank=True, null=True, max_length=500)
    status = models.CharField(max_length=20, choices=[
        ('in_progress', 'In Progress'),
        ('active', 'Active'),
        ('rejected', 'Rejected'),
        ('expired', 'Expired'),
    ], default='in_progress')
    created_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_expired(self):
        if self.activated_at:
            return timezone.now() > self.activated_at + datetime.timedelta(hours=24)
        return False

    @property
    def hours_remaining(self):
        if self.activated_at:
            remaining = (self.activated_at + datetime.timedelta(hours=24)) - timezone.now()
            return max(0, int(remaining.total_seconds() // 3600))
        return 0

    def __str__(self):
        return f"TempChat: {self.initiator.username} ↔ {self.target.username} ({self.status})"

class TempChatMessage(models.Model):
    temp_chat = models.ForeignKey(TempChat, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.sender.username}: {self.content[:50]}"

class Notification(models.Model):
    """User notification for intro steps, connections, messages etc."""
    NOTIF_TYPES = [
        ('intro_forward', 'Intro Forward Request'),
        ('intro_accepted', 'Intro Accepted'),
        ('intro_rejected', 'Intro Rejected'),
        ('connection_new', 'New Connection'),
        ('connection_removed', 'Connection Removed'),
        ('temp_chat_request', 'Temp Chat Request'),
        ('temp_chat_active', 'Temp Chat Activated'),
        ('message', 'New Message'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    notif_type = models.CharField(max_length=30, choices=NOTIF_TYPES)
    title = models.CharField(max_length=200)
    body = models.TextField(max_length=500, blank=True)
    link = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.notif_type}] {self.user.username}: {self.title}"


def default_otp_expiry():
    return timezone.now() + datetime.timedelta(minutes=5)


class EmailOTP(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='email_otps')
    otp = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_otp_expiry)

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"OTP for {self.user.username} ({'expired' if self.is_expired else 'valid'})"
