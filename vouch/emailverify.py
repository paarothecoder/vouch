import random
import string
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from .models import EmailOTP


def generate_otp(user, request=None):
    """Generate a 6-digit OTP, save it, and email it to the user."""
    # Invalidate any existing OTPs for this user
    EmailOTP.objects.filter(user=user).delete()

    otp_code = ''.join(random.choices(string.digits, k=6))
    EmailOTP.objects.create(user=user, otp=otp_code)
    print(otp_code)

    message = f'Your verification code is: {otp_code}\n\nThis code expires in 5 minutes.'
    
    if request:
        verify_url = request.build_absolute_uri(f'/verify-email/?user_id={user.id}&otp={otp_code}')
        message += f'\n\nOr click here to verify automatically: {verify_url}'

    send_mail(
        subject='Vouch — Verify your email',
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
    return otp_code


def verify_otp(user, otp_code):
    """
    Check the OTP. Returns (success: bool, error_message: str|None).
    On success, marks the user as email-verified.
    """
    otp_record = EmailOTP.objects.filter(user=user, otp=otp_code).first()

    if not otp_record:
        return False, 'Invalid verification code.'

    if otp_record.is_expired:
        otp_record.delete()
        return False, 'This code has expired. Please request a new one.'

    # Success — mark verified, clean up
    user.is_email_verified = True
    user.save(update_fields=['is_email_verified'])
    EmailOTP.objects.filter(user=user).delete()
    return True, None


def resend_otp(user, request=None):
    """Delete old OTPs and generate a fresh one."""
    return generate_otp(user, request)
