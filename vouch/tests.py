from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
import datetime
from .models import Invite, Connection, Post, IntroChain, IntroStep

User = get_user_model()

class VouchNetworkTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username='usera', email='a@test.com', password='password123', first_name='A')
        self.user_b = User.objects.create_user(username='userb', email='b@test.com', password='password123', first_name='B')
        self.user_c = User.objects.create_user(username='userc', email='c@test.com', password='password123', first_name='C')
        
    def test_invite_creation_and_registration(self):
        self.client.login(username='usera', password='password123')
        
        response = self.client.post(reverse('send_invite'), {'email': 'd@test.com'})
        self.assertEqual(response.status_code, 302)
        
        invite = Invite.objects.get(email='d@test.com')
        self.assertEqual(invite.sender, self.user_a)
        self.assertEqual(invite.status, 'pending')
        self.assertFalse(invite.is_expired)
        
        self.client.logout()
        
        # Inject our mock prereg_otp into the session so registration succeeds
        session = self.client.session
        session['prereg_otp_d@test.com'] = '123456'
        session['prereg_otp_time_d@test.com'] = timezone.now().timestamp()
        session.save()
        
        reg_data = {
            'username': 'userd', 'first_name': 'D', 'last_name': 'Test',
            'password': 'password123', 'password_confirm': 'password123',
            'account_type': 'professional',
            'otp': '123456',
        }
        res = self.client.post(reverse('register', args=[invite.token]), reg_data)
        self.assertEqual(res.status_code, 302)
        
        invite.refresh_from_db()
        self.assertEqual(invite.status, 'accepted')
        
        user_d = User.objects.get(username='userd')
        self.assertTrue(Connection.objects.filter(user1=self.user_a, user2=user_d).exists())
        self.assertTrue(Connection.objects.filter(user1=user_d, user2=self.user_a).exists())

    def test_invite_expiry(self):
        invite = Invite.objects.create(
            sender=self.user_a, email='expired@test.com',
            expires_at=timezone.now() - datetime.timedelta(minutes=1)
        )
        self.assertTrue(invite.is_expired)
        response = self.client.get(reverse('register', args=[invite.token]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('expired', str(response.content).lower())

    def test_unlimited_invites(self):
        self.client.login(username='usera', password='password123')
        for i in range(10):
            self.client.post(reverse('send_invite'), {'email': f'person{i}@test.com'})
        self.assertEqual(Invite.objects.filter(sender=self.user_a).count(), 10)

    def test_feed_on_own_profile(self):
        Connection.objects.create(user1=self.user_a, user2=self.user_b)
        Connection.objects.create(user1=self.user_b, user2=self.user_a)
        Post.objects.create(author=self.user_a, content="A Post")
        Post.objects.create(author=self.user_b, content="B Post")
        Post.objects.create(author=self.user_c, content="C Post")
        
        self.client.login(username='usera', password='password123')
        response = self.client.get(reverse('profile', args=['usera']))
        content = str(response.content)
        self.assertIn("A Post", content)
        self.assertIn("B Post", content)
        self.assertNotIn("C Post", content)
        
    def test_unauthenticated_landing(self):
        response = self.client.get(reverse('landing'))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Sign In", str(response.content))
        self.client.login(username='usera', password='password123')
        response = self.client.get(reverse('landing'))
        self.assertEqual(response.status_code, 302)

    def test_network_search(self):
        user_d = User.objects.create_user(username='userd', email='d@test.com', password='password123', first_name='D')
        Connection.objects.create(user1=self.user_a, user2=self.user_b)
        Connection.objects.create(user1=self.user_b, user2=self.user_a)
        Connection.objects.create(user1=self.user_b, user2=self.user_c)
        Connection.objects.create(user1=self.user_c, user2=self.user_b)
        Connection.objects.create(user1=self.user_c, user2=user_d)
        Connection.objects.create(user1=user_d, user2=self.user_c)
        
        from .utils import find_shortest_path
        path = find_shortest_path(self.user_a.id, user_d.id)
        self.assertEqual(len(path), 4)
        
        self.client.login(username='usera', password='password123')
        response = self.client.get(reverse('network') + '?q=D')
        self.assertEqual(response.status_code, 200)
        self.assertIn("D", str(response.content))

    def test_cascading_intro_chain_with_target_acceptance(self):
        """A->B->C: A requests intro to C. B forwards. C must accept/reject."""
        Connection.objects.create(user1=self.user_a, user2=self.user_b)
        Connection.objects.create(user1=self.user_b, user2=self.user_a)
        Connection.objects.create(user1=self.user_b, user2=self.user_c)
        Connection.objects.create(user1=self.user_c, user2=self.user_b)
        
        # A starts intro chain to C
        self.client.login(username='usera', password='password123')
        self.client.post(reverse('request_intro', args=['userc']), {'message': 'Want to connect'})
        
        chain = IntroChain.objects.get(initiator=self.user_a, target=self.user_c)
        self.assertEqual(chain.status, 'in_progress')
        
        # Should have 2 steps: B forwards (not final), C accepts/rejects (final)
        steps = list(chain.steps.order_by('order'))
        self.assertEqual(len(steps), 2)
        
        step_b = steps[0]
        self.assertEqual(step_b.to_user, self.user_b)
        self.assertFalse(step_b.is_final)
        self.assertEqual(step_b.status, 'pending')
        
        step_c = steps[1]
        self.assertEqual(step_c.to_user, self.user_c)
        self.assertTrue(step_c.is_final)
        self.assertEqual(step_c.status, 'waiting')
        
        # B accepts (forwards) - should NOT create connection yet
        self.client.login(username='userb', password='password123')
        self.client.post(reverse('manage_intro', args=[step_b.id, 'accept']))
        
        step_b.refresh_from_db()
        self.assertEqual(step_b.status, 'accepted')
        
        # Connection should NOT exist yet
        self.assertFalse(Connection.objects.filter(user1=self.user_a, user2=self.user_c).exists())
        
        # C's step should now be pending
        step_c.refresh_from_db()
        self.assertEqual(step_c.status, 'pending')
        
        # C accepts - NOW connection should be created
        self.client.login(username='userc', password='password123')
        self.client.post(reverse('manage_intro', args=[step_c.id, 'accept']))
        
        chain.refresh_from_db()
        self.assertEqual(chain.status, 'completed')
        self.assertTrue(Connection.objects.filter(user1=self.user_a, user2=self.user_c).exists())
        self.assertTrue(Connection.objects.filter(user1=self.user_c, user2=self.user_a).exists())

    def test_target_can_reject_intro(self):
        """Target (C) can reject the final intro step."""
        Connection.objects.create(user1=self.user_a, user2=self.user_b)
        Connection.objects.create(user1=self.user_b, user2=self.user_a)
        Connection.objects.create(user1=self.user_b, user2=self.user_c)
        Connection.objects.create(user1=self.user_c, user2=self.user_b)
        
        self.client.login(username='usera', password='password123')
        self.client.post(reverse('request_intro', args=['userc']), {'message': 'Hi'})
        
        chain = IntroChain.objects.get(initiator=self.user_a, target=self.user_c)
        steps = list(chain.steps.order_by('order'))
        
        # B forwards
        self.client.login(username='userb', password='password123')
        self.client.post(reverse('manage_intro', args=[steps[0].id, 'accept']))
        
        # C rejects
        steps[1].refresh_from_db()
        self.client.login(username='userc', password='password123')
        self.client.post(reverse('manage_intro', args=[steps[1].id, 'reject']))
        
        chain.refresh_from_db()
        self.assertEqual(chain.status, 'rejected')
        self.assertFalse(Connection.objects.filter(user1=self.user_a, user2=self.user_c).exists())

    def test_remove_connection(self):
        """Users can remove connections."""
        Connection.objects.create(user1=self.user_a, user2=self.user_b)
        Connection.objects.create(user1=self.user_b, user2=self.user_a)
        
        self.client.login(username='usera', password='password123')
        response = self.client.post(reverse('remove_connection', args=['userb']))
        self.assertEqual(response.status_code, 302)
        
        self.assertFalse(Connection.objects.filter(user1=self.user_a, user2=self.user_b).exists())
        self.assertFalse(Connection.objects.filter(user1=self.user_b, user2=self.user_a).exists())
