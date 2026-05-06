import random
import string
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.utils import timezone
from .models import (Invite, Connection, ConnectionRequest, Post, IntroChain, IntroStep,
                     Message, TempChat, TempChatMessage, Notification)
from .forms import RegisterForm, InviteForm, PostForm
from .utils import find_shortest_path, find_all_shortest_paths
from .emailverify import generate_otp, verify_otp, resend_otp as resend_otp_func

from django.http import JsonResponse
from django.core.mail import send_mail
from django.conf import settings

User = get_user_model()

MAX_CHAIN_LOGS = 3  # Only keep the 3 most recent intro chain logs

def _notify(user, notif_type, title, body='', link=''):
    """Create a notification for a user."""
    Notification.objects.create(user=user, notif_type=notif_type, title=title, body=body, link=link)

def _cleanup_old_chains(user):
    """Keep only the most recent MAX_CHAIN_LOGS chains, delete the rest."""
    chain_ids = list(
        IntroChain.objects.filter(initiator=user)
        .order_by('-created_at')
        .values_list('id', flat=True)
    )
    if len(chain_ids) > MAX_CHAIN_LOGS:
        old_ids = chain_ids[MAX_CHAIN_LOGS:]
        IntroChain.objects.filter(id__in=old_ids).delete()

# ── Landing ─────────────────────────────────────────────────────────
def landing(request):
    if request.user.is_authenticated:
        return redirect('profile', username=request.user.username)
    return render(request, 'landing.html')

# ── Auth ────────────────────────────────────────────────────────────
def login_view(request):
    """Custom login view that blocks unverified users."""
    if request.user.is_authenticated:
        return redirect('profile', username=request.user.username)

    if request.method == 'POST':
        username = request.POST.get('username', '')
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)

        if user is not None:
            if not user.is_email_verified:
                # Resend OTP so they can verify
                generate_otp(user, request)
                request.session['verify_user_id'] = user.id
                messages.warning(request, 'Your email is not verified. A new verification code has been sent.')
                return redirect('verify_email')
            login(request, user)
            return redirect('profile', username=user.username)
        else:
            messages.error(request, 'Invalid username or password.')

    return render(request, 'auth/login.html')


def verify_email_view(request):
    """OTP verification page."""
    user_id = request.GET.get('user_id') or request.session.get('verify_user_id')
    otp_code = request.GET.get('otp')
    
    if not user_id:
        messages.error(request, 'No pending verification. Please register or log in.')
        return redirect('login')

    user = get_object_or_404(User, id=user_id)

    if user.is_email_verified:
        request.session.pop('verify_user_id', None)
        messages.info(request, 'Your email is already verified.')
        return redirect('login')

    if otp_code:
        success, error = verify_otp(user, otp_code)
        if success:
            request.session.pop('verify_user_id', None)
            login(request, user)
            messages.success(request, 'Email verified! Welcome to Vouch.')
            return redirect('profile', username=user.username)
        else:
            messages.error(request, error)

    if request.method == 'POST':
        post_otp_code = request.POST.get('otp', '').strip()
        success, error = verify_otp(user, post_otp_code)
        if success:
            request.session.pop('verify_user_id', None)
            login(request, user)
            messages.success(request, 'Email verified! Welcome to Vouch.')
            return redirect('profile', username=user.username)
        else:
            messages.error(request, error)

    # Mask the email for display
    email = user.email
    at_idx = email.index('@')
    masked = email[0] + '*' * (at_idx - 1) + email[at_idx:]

    # Ensure user_id is set in session if accessed via link
    if not request.session.get('verify_user_id'):
        request.session['verify_user_id'] = user.id

    return render(request, 'auth/verify_email.html', {'masked_email': masked})


def resend_otp_view(request):
    """Resend a fresh OTP."""
    user_id = request.session.get('verify_user_id')
    if not user_id:
        return redirect('login')

    user = get_object_or_404(User, id=user_id)
    resend_otp_func(user, request)
    messages.success(request, 'A new verification code has been sent to your email.')
    return redirect('verify_email')


def send_prereg_otp(request):
    """AJAX endpoint to send OTP during registration."""
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        if not email:
            return JsonResponse({'success': False, 'error': 'Email is required.'})
            
        # Optional: check if email is already taken here
        if User.objects.filter(email=email).exists():
            return JsonResponse({'success': False, 'error': 'Email is already registered.'})
            
        otp_code = ''.join(random.choices(string.digits, k=6))
        
        # Store in session for validation during actual registration
        request.session[f'prereg_otp_{email}'] = otp_code
        request.session[f'prereg_otp_time_{email}'] = timezone.now().timestamp()
        
        send_mail(
            subject='Vouch — Verify your email',
            message=f'Your verification code is: {otp_code}\n\nThis code expires in 5 minutes.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
        return JsonResponse({'success': True, 'message': 'Verification code sent.'})
        
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


def register(request, token):
    invite = Invite.objects.filter(token=token).first()
    if not invite:
        return render(request, 'auth/invite_invalid.html', {'reason': 'not_found'})
    if invite.status == 'accepted':
        return render(request, 'auth/invite_invalid.html', {'reason': 'used'})
    if invite.is_expired:
        invite.status = 'expired'
        invite.save()
        return render(request, 'auth/invite_invalid.html', {'reason': 'expired'})
    
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        otp_code = request.POST.get('otp', '').strip()
        email = invite.email
        
        # Verify OTP
        session_otp = request.session.get(f'prereg_otp_{email}')
        session_time = request.session.get(f'prereg_otp_time_{email}', 0)
        
        # Simple expiration check: 5 minutes = 300 seconds
        is_expired = (timezone.now().timestamp() - session_time) > 300
        
        if not otp_code or not session_otp or is_expired or session_otp != otp_code:
            form.add_error(None, "Invalid or expired verification code.")
            
        if form.is_valid():
            user = form.save(commit=False)
            user.is_email_verified = True # Pre-verified
            user.save()
            
            # Clear session
            request.session.pop(f'prereg_otp_{email}', None)
            request.session.pop(f'prereg_otp_time_{email}', None)
            
            invite.status = 'accepted'
            invite.save()
            Connection.objects.create(user1=invite.sender, user2=user)
            Connection.objects.create(user1=user, user2=invite.sender)
            _notify(invite.sender, 'connection_new',
                    f'{user.first_name} {user.last_name} joined via your invite!',
                    link=f'/u/{user.username}/')
                    
            login(request, user)
            messages.success(request, 'Email verified! Welcome to Vouch.')
            return redirect('profile', username=user.username)
    else:
        form = RegisterForm(initial={'email': invite.email})
    
    remaining = invite.expires_at - timezone.now()
    minutes_left = max(0, int(remaining.total_seconds() // 60))
    return render(request, 'auth/register.html', {'form': form, 'invite': invite, 'minutes_left': minutes_left})

# ── Notifications ───────────────────────────────────────────────────
@login_required
def notifications_view(request):
    notifs = Notification.objects.filter(user=request.user)[:50]
    # Mark all as read
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return render(request, 'notifications.html', {'notifs': notifs})

# ── Profile Tab ─────────────────────────────────────────────────────
@login_required
def profile(request, username):
    profile_user = get_object_or_404(User, username=username)
    is_connected = Connection.objects.filter(user1=request.user, user2=profile_user).exists()
    is_self = profile_user == request.user
    
    existing_chain = IntroChain.objects.filter(
        initiator=request.user, target=profile_user, status='in_progress'
    ).first()
    
    all_paths = []
    if not is_connected and not is_self:
        all_paths = find_all_shortest_paths(request.user.id, profile_user.id)
    
    active_temp_chat = TempChat.objects.filter(
        Q(initiator=request.user, target=profile_user) | Q(initiator=profile_user, target=request.user),
        status='active'
    ).first()
    if active_temp_chat and active_temp_chat.is_expired:
        active_temp_chat.status = 'expired'
        active_temp_chat.save()
        active_temp_chat = None
    
    pending_temp_chat = TempChat.objects.filter(
        initiator=request.user, target=profile_user, status='in_progress'
    ).first()
    
    their_connections = []
    if is_connected or is_self:
        their_connections = Connection.objects.filter(
            user1=profile_user
        ).select_related('user2')
    
    posts = []
    form = None
    if is_self:
        connection_ids = Connection.objects.filter(user1=request.user).values_list('user2_id', flat=True)
        authors = list(connection_ids) + [request.user.id]
        posts = Post.objects.filter(author_id__in=authors).order_by('-created_at')
        if request.method == 'POST':
            form = PostForm(request.POST)
            if form.is_valid():
                Post.objects.create(author=request.user, content=form.cleaned_data['content'])
                messages.success(request, "Broadcast sent to your network.")
                return redirect('profile', username=request.user.username)
        else:
            form = PostForm()
    
    return render(request, 'profile.html', {
        'profile_user': profile_user,
        'is_connected': is_connected,
        'is_self': is_self,
        'existing_chain': existing_chain,
        'all_paths': all_paths,
        'their_connections': their_connections,
        'active_temp_chat': active_temp_chat,
        'pending_temp_chat': pending_temp_chat,
        'posts': posts,
        'form': form,
    })

# ── Network Tab ─────────────────────────────────────────────────────
@login_required
def network(request):
    connections = Connection.objects.filter(user1=request.user).select_related('user2')
    connected_ids = set(connections.values_list('user2_id', flat=True))
    
    pending_intros = IntroStep.objects.filter(
        to_user=request.user, status='pending'
    ).select_related('chain', 'chain__initiator', 'chain__target', 'from_user', 'next_user')
    
    # Only show last 3 chain logs
    my_chains = IntroChain.objects.filter(initiator=request.user).order_by('-created_at')[:MAX_CHAIN_LOGS]
    # Clean up old ones
    _cleanup_old_chains(request.user)
    
    query = request.GET.get('q', '')
    search_results = []
    if query:
        users = User.objects.filter(
            Q(first_name__icontains=query) | Q(last_name__icontains=query) |
            Q(title__icontains=query) | Q(company__icontains=query)
        ).exclude(id=request.user.id).exclude(id__in=connected_ids)
        
        for u in users:
            path = find_shortest_path(request.user.id, u.id)
            if path:
                search_results.append({'user': u, 'degrees': len(path) - 1, 'path': path})
        search_results.sort(key=lambda x: x['degrees'])
    
    return render(request, 'network.html', {
        'connections': connections,
        'pending_intros': pending_intros,
        'my_chains': my_chains,
        'query': query,
        'search_results': search_results,
    })

# ── Invites Tab ─────────────────────────────────────────────────────
@login_required
def invites(request):
    sent_invites = Invite.objects.filter(sender=request.user).order_by('-created_at')
    for inv in sent_invites:
        if inv.status == 'pending' and inv.is_expired:
            inv.status = 'expired'
            inv.save()
    sent_invites = Invite.objects.filter(sender=request.user).order_by('-created_at')
    return render(request, 'invites.html', {'sent_invites': sent_invites})

@login_required
def send_invite(request):
    if request.method == 'POST':
        form = InviteForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            if User.objects.filter(email=email).exists():
                messages.error(request, "A user with this email already exists.")
            else:
                invite = form.save(commit=False)
                invite.sender = request.user
                invite.save()
                invite_link = request.build_absolute_uri(f'/register/{invite.token}/')
                messages.success(request, f"Invite link (valid for 60 minutes): {invite_link}")
        else:
            messages.error(request, "Invalid email address.")
    return redirect('invites')

# ── Chat (Direct Connections) ───────────────────────────────────────
@login_required
def chat_list(request):
    connections = Connection.objects.filter(user1=request.user).select_related('user2')
    conversations = []
    for conn in connections:
        other = conn.user2
        last_msg = Message.objects.filter(
            Q(sender=request.user, receiver=other) | Q(sender=other, receiver=request.user)
        ).order_by('-created_at').first()
        unread = Message.objects.filter(sender=other, receiver=request.user, is_read=False).count()
        conversations.append({'user': other, 'last_message': last_msg, 'unread': unread})
    
    active_temp_chats = TempChat.objects.filter(
        Q(initiator=request.user) | Q(target=request.user), status='active'
    ).select_related('initiator', 'target')
    temp_conversations = []
    for tc in active_temp_chats:
        if tc.is_expired:
            tc.status = 'expired'
            tc.save()
            continue
        other = tc.target if tc.initiator == request.user else tc.initiator
        last_msg = tc.messages.order_by('-created_at').first()
        temp_conversations.append({
            'user': other, 'temp_chat': tc, 'last_message': last_msg,
            'hours_remaining': tc.hours_remaining,
        })
    
    conversations.sort(key=lambda c: (
        -c['unread'], -(c['last_message'].created_at.timestamp() if c['last_message'] else 0)
    ))
    
    return render(request, 'chat/chat_list.html', {
        'conversations': conversations, 'temp_conversations': temp_conversations,
    })

@login_required
def chat_conversation(request, username):
    other_user = get_object_or_404(User, username=username)
    if not Connection.objects.filter(user1=request.user, user2=other_user).exists():
        messages.error(request, "You can only chat with direct connections.")
        return redirect('chat_list')
    
    if request.method == 'POST':
        content = request.POST.get('content', '').strip()
        if content:
            Message.objects.create(sender=request.user, receiver=other_user, content=content)
            _notify(other_user, 'message',
                    f'New message from {request.user.first_name}',
                    body=content[:100], link=f'/chat/{request.user.username}/')
            return redirect('chat_conversation', username=username)
    
    Message.objects.filter(sender=other_user, receiver=request.user, is_read=False).update(is_read=True)
    chat_messages = Message.objects.filter(
        Q(sender=request.user, receiver=other_user) | Q(sender=other_user, receiver=request.user)
    ).select_related('sender').order_by('-created_at')[:100]
    chat_messages = list(reversed(chat_messages))
    
    return render(request, 'chat/conversation.html', {
        'other_user': other_user, 'chat_messages': chat_messages, 'is_temp': False,
    })

# ── Chat Connect (receiver can make sender a connection) ────────────
@login_required
def chat_connect(request, username):
    """Allow a chat participant to add the other as a connection."""
    other_user = get_object_or_404(User, username=username)
    
    if request.method == 'POST':
        if Connection.objects.filter(user1=request.user, user2=other_user).exists():
            messages.info(request, "You're already connected.")
        else:
            Connection.objects.get_or_create(user1=request.user, user2=other_user)
            Connection.objects.get_or_create(user1=other_user, user2=request.user)
            _notify(other_user, 'connection_new',
                    f'{request.user.first_name} {request.user.last_name} added you as a connection!',
                    link=f'/u/{request.user.username}/')
            messages.success(request, f"You are now connected with {other_user.first_name}!")
    
    # Redirect back to wherever they came from
    next_url = request.POST.get('next', f'/chat/{username}/')
    return redirect(next_url)

# ── Temp Chat ───────────────────────────────────────────────────────
@login_required
def request_temp_chat(request, username):
    target = get_object_or_404(User, username=username)
    
    if target == request.user:
        return redirect('profile', username=username)
    if Connection.objects.filter(user1=request.user, user2=target).exists():
        messages.info(request, "You're already connected — use regular chat.")
        return redirect('chat_conversation', username=username)
    
    existing = TempChat.objects.filter(
        initiator=request.user, target=target, status__in=['in_progress', 'active']
    ).first()
    if existing:
        if existing.status == 'active' and not existing.is_expired:
            return redirect('temp_chat_conversation', chat_id=existing.id)
        elif existing.status == 'in_progress':
            messages.info(request, "You already have a temp chat request in progress.")
            return redirect('profile', username=username)
    
    if request.method == 'POST':
        path_index = int(request.POST.get('path_index', 0))
        msg = request.POST.get('message', '')
        
        all_paths = find_all_shortest_paths(request.user.id, target.id)
        if not all_paths or path_index >= len(all_paths):
            messages.error(request, "No connection path found.")
            return redirect('profile', username=username)
        
        path = all_paths[path_index]
        if len(path) < 3:
            messages.error(request, "No intermediary path found.")
            return redirect('profile', username=username)
        
        temp_chat = TempChat.objects.create(initiator=request.user, target=target, message=msg)
        chain = IntroChain.objects.create(
            initiator=request.user, target=target, message=f"[TEMP CHAT REQUEST] {msg}",
        )
        for i in range(len(path) - 2):
            IntroStep.objects.create(
                chain=chain, from_user=path[i], to_user=path[i+1], next_user=path[i+2],
                order=i, status='pending' if i == 0 else 'waiting', is_final=False
            )
        IntroStep.objects.create(
            chain=chain, from_user=path[-2], to_user=path[-1], next_user=path[0],
            order=len(path)-2, status='waiting', is_final=True
        )
        temp_chat.message = f"chain:{chain.id}|{msg}"
        temp_chat.save()
        
        # Notify first intermediary
        _notify(path[1], 'temp_chat_request',
                f'{request.user.first_name} wants a temp chat with {target.first_name}',
                body='Please forward this request.', link='/network/')
        
        messages.success(request, "Temp chat request sent through the chain!")
        _cleanup_old_chains(request.user)
    return redirect('profile', username=username)

@login_required
def temp_chat_conversation(request, chat_id):
    temp_chat = get_object_or_404(TempChat, id=chat_id, status='active')
    
    if request.user not in [temp_chat.initiator, temp_chat.target]:
        messages.error(request, "You don't have access to this chat.")
        return redirect('chat_list')
    
    if temp_chat.is_expired:
        temp_chat.status = 'expired'
        temp_chat.save()
        messages.info(request, "This temp chat has expired.")
        return redirect('chat_list')
    
    other_user = temp_chat.target if temp_chat.initiator == request.user else temp_chat.initiator
    is_connected = Connection.objects.filter(user1=request.user, user2=other_user).exists()
    
    if request.method == 'POST':
        content = request.POST.get('content', '').strip()
        if content:
            TempChatMessage.objects.create(temp_chat=temp_chat, sender=request.user, content=content)
            return redirect('temp_chat_conversation', chat_id=chat_id)
    
    chat_messages = temp_chat.messages.select_related('sender').all()
    
    return render(request, 'chat/conversation.html', {
        'other_user': other_user,
        'chat_messages': chat_messages,
        'is_temp': True,
        'temp_chat': temp_chat,
        'is_connected': is_connected,
    })

@login_required
def end_temp_chat(request, chat_id):
    temp_chat = get_object_or_404(TempChat, id=chat_id, status='active')
    if request.method == 'POST' and request.user == temp_chat.target:
        temp_chat.status = 'expired'
        temp_chat.save()
        messages.info(request, "You have ended the temporary chat.")
        return redirect('chat_list')
    return redirect('temp_chat_conversation', chat_id=chat_id)

# ── Explore ─────────────────────────────────────────────────────────
@login_required
def explore(request, username):
    target_user = get_object_or_404(User, username=username)
    trail_param = request.GET.get('trail', '')
    trail_usernames = [u for u in trail_param.split(',') if u] if trail_param else []
    trail_users = list(User.objects.filter(username__in=trail_usernames))
    trail_users.sort(key=lambda u: trail_usernames.index(u.username))
    connections = Connection.objects.filter(user1=target_user).select_related('user2')
    
    return render(request, 'explore.html', {
        'target_user': target_user, 'connections': connections,
        'trail_users': trail_users, 'is_self': target_user == request.user,
    })

# ── Intro Chain ─────────────────────────────────────────────────────
@login_required
def request_intro(request, username):
    target = get_object_or_404(User, username=username)
    
    if target == request.user:
        return redirect('profile', username=username)
    if Connection.objects.filter(user1=request.user, user2=target).exists():
        messages.info(request, "You are already connected.")
        return redirect('profile', username=username)
    if IntroChain.objects.filter(initiator=request.user, target=target, status='in_progress').exists():
        messages.info(request, "You already have an intro request in progress.")
        return redirect('profile', username=username)
    
    if request.method == 'POST':
        path_index = int(request.POST.get('path_index', 0))
        msg = request.POST.get('message', '')
        
        all_paths = find_all_shortest_paths(request.user.id, target.id)
        if not all_paths or path_index >= len(all_paths):
            messages.error(request, "No connection path found.")
            return redirect('profile', username=username)
        
        path = all_paths[path_index]
        if len(path) < 3:
            messages.error(request, "Path too short.")
            return redirect('profile', username=username)
        
        chain = IntroChain.objects.create(initiator=request.user, target=target, message=msg)
        for i in range(len(path) - 2):
            IntroStep.objects.create(
                chain=chain, from_user=path[i], to_user=path[i+1], next_user=path[i+2],
                order=i, status='pending' if i == 0 else 'waiting', is_final=False
            )
        IntroStep.objects.create(
            chain=chain, from_user=path[-2], to_user=path[-1], next_user=path[0],
            order=len(path)-2, status='waiting', is_final=True
        )
        
        # Notify first intermediary
        _notify(path[1], 'intro_forward',
                f'{request.user.first_name} wants an intro to {target.first_name}',
                body='Please forward this introduction.', link='/network/')
        
        messages.success(request, f"Intro chain started through {len(path) - 2} people!")
        _cleanup_old_chains(request.user)
    return redirect('profile', username=username)

@login_required
def manage_intro(request, step_id, action):
    step = get_object_or_404(IntroStep, id=step_id, to_user=request.user, status='pending')
    chain = step.chain
    
    if action == 'accept':
        step.status = 'accepted'
        step.save()
        
        if step.is_final:
            is_temp = chain.message and chain.message.startswith('[TEMP CHAT REQUEST]')
            
            if is_temp:
                temp_chat = TempChat.objects.filter(
                    initiator=chain.initiator, target=chain.target, status='in_progress'
                ).first()
                if temp_chat:
                    temp_chat.status = 'active'
                    temp_chat.activated_at = timezone.now()
                    temp_chat.save()
                    chain.status = 'completed'
                    chain.save()
                    _notify(chain.initiator, 'temp_chat_active',
                            f'{chain.target.first_name} accepted your temp chat request!',
                            link=f'/temp-chat/{temp_chat.id}/')
                    messages.success(request, f"24-hour temp chat with {chain.initiator.first_name} is now active!")
                    return redirect('temp_chat_conversation', chat_id=temp_chat.id)
            else:
                Connection.objects.get_or_create(user1=chain.initiator, user2=chain.target)
                Connection.objects.get_or_create(user1=chain.target, user2=chain.initiator)
                chain.status = 'completed'
                chain.save()
                _notify(chain.initiator, 'intro_accepted',
                        f'{chain.target.first_name} accepted your connection!',
                        link=f'/u/{chain.target.username}/')
                _notify(chain.target, 'connection_new',
                        f'You are now connected with {chain.initiator.first_name}!',
                        link=f'/u/{chain.initiator.username}/')
                messages.success(request, f"You are now connected with {chain.initiator.first_name} {chain.initiator.last_name}!")
        else:
            next_step = chain.steps.filter(order=step.order + 1).first()
            if next_step:
                next_step.status = 'pending'
                next_step.save()
                _notify(next_step.to_user, 'intro_forward',
                        f'{step.from_user.first_name} asks you to forward an intro',
                        body=f'Forward to {next_step.next_user.first_name}', link='/network/')
                messages.success(request, f"Forwarded the introduction to {step.next_user.first_name}.")
    
    elif action == 'reject':
        step.status = 'rejected'
        step.save()
        chain.status = 'rejected'
        chain.save()
        
        _notify(chain.initiator, 'intro_rejected',
                f'Your intro request to {chain.target.first_name} was declined.',
                link=f'/u/{chain.target.username}/')
        
        if chain.message and chain.message.startswith('[TEMP CHAT REQUEST]'):
            TempChat.objects.filter(
                initiator=chain.initiator, target=chain.target, status='in_progress'
            ).update(status='rejected')
        
        messages.info(request, "Request declined.")
    
    return redirect('network')

# ── Remove Connection ───────────────────────────────────────────────
@login_required
def remove_connection(request, username):
    other_user = get_object_or_404(User, username=username)
    if request.method == 'POST':
        Connection.objects.filter(user1=request.user, user2=other_user).delete()
        Connection.objects.filter(user1=other_user, user2=request.user).delete()
        _notify(other_user, 'connection_removed',
                f'{request.user.first_name} {request.user.last_name} disconnected from you.')
        messages.success(request, f"You have disconnected from {other_user.first_name} {other_user.last_name}.")
    return redirect('network')
