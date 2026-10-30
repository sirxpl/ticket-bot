"""Real event timing, range isolation, empty states, and safe avatar rendering."""
import datetime as dt
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.analytics import build_analytics, avatar_url, timestamp, duration
from test_dashboard_management import DashboardFixture

NOW = dt.datetime(2026, 10, 30, 12, tzinfo=dt.timezone.utc).timestamp()

def event(tid, action, ago=0, **fields):
    return {'ticket_id':str(tid),'action':action,'timestamp':NOW-ago,**fields}

class AnalyticsTests(unittest.TestCase):
    def test_period_excludes_old_staff_and_future_events_and_uses_event_day(self):
        logs = [event(1,'created',9*86400,creator={'id':'1'}), event(1,'staff_message',9*86400-10,actor={'id':'2'}),
                event(1,'closed',3600,executor={'id':'2','name':'Helper'}),event(2,'created',-3600)]
        result = build_analytics(logs,[],7,now=NOW)
        self.assertEqual(result['period_created'],0)
        self.assertEqual(result['period_closed'],1)
        self.assertEqual(result['metrics']['reply']['count'],0)
        self.assertEqual(result['baselines']['reply'],10)
        self.assertEqual(result['staff_leaderboard'][0]['responses'],0)
        self.assertEqual(result['staff_leaderboard'][0]['closed'],1)
        self.assertEqual(result['daily'][-1]['closed'],1)

    def test_first_start_reply_and_close_metrics_ignore_repeat_starts(self):
        logs=[event(1,'created',120,category_label='Support',creator={'id':'1'}),event(1,'ticket_started',100),
              event(1,'ticket_started',50),event(1,'staff_message',90,message_id='9',actor={'id':'2','name':'Helper'}),
              event(1,'staff_message',90,message_id='9',actor={'id':'2'}),event(1,'member_message',80),
              event(1,'closed',0,executor={'id':'2','name':'Helper'},reason='Done',close_kind='Closed by staff')]
        result=build_analytics(logs,[],7,now=NOW)
        self.assertEqual([result['metrics'][k]['average'] for k in ['claim','reply','close']],[20,30,120])
        self.assertEqual(result['staff_leaderboard'][0]['responses'],1)
        self.assertEqual(result['staff_leaderboard'][0]['avg_reply'],30)
        self.assertEqual(result['lifecycle'][1]['percent'],100)
        self.assertEqual(result['close_reasons'],[{'label':'Done','value':1}])
        self.assertEqual(sum(sum(day) for day in result['heatmap']),1)

    def test_category_filter_and_live_queue_are_independent_of_period(self):
        logs=[event(1,'created',20*86400,category_label='A'),event(2,'created',50,category_label='B')]
        active=[{'channel_id':'1','created_at':NOW-20*86400,'category_label':'A'},
                {'channel_id':'2','created_at':NOW-50,'category_label':'B'}]
        result=build_analytics(logs,active,7,'A',NOW)
        self.assertEqual(result['period_created'],0)
        self.assertEqual(result['open_now'],1)
        self.assertEqual(result['oldest_open'],20*86400)
        self.assertEqual(result['queue_age'][-1]['value'],1)
        self.assertEqual(result['categories'],['A','B'])

    def test_empty_and_legacy_events_do_not_invent_response_times(self):
        result=build_analytics([event(1,'closed',1,executor={'id':'1'})],[],7,now=NOW)
        self.assertEqual(result['all_time_closed'],1)
        self.assertIsNone(result['metrics']['reply']['average'])
        self.assertIsNone(result['metrics']['close']['average'])
        self.assertEqual(result['oldest_open'],None)
        self.assertEqual(result['daily'][-1]['reply'],None)
        self.assertEqual(result['heatmap_max'],0)

    def test_owner_and_bot_closes_are_excluded_from_staff_board(self):
        logs=[event(1,'created',120,creator={'id':'1'}),event(1,'closed',1,executor={'id':'1'}),
              event(2,'created',120),event(2,'closed',1,executor={'id':'2','bot':True})]
        self.assertEqual(build_analytics(logs,[],7,now=NOW)['staff_leaderboard'],[])

    def test_duplicate_close_does_not_inflate_totals_or_endings(self):
        close=event(1,'closed',1,executor={'id':'2'})
        result=build_analytics([event(1,'created',120),close,close],[],now=NOW)
        self.assertEqual(result['period_closed'],1)
        self.assertEqual(result['endings'][0]['value'],1)
        self.assertEqual(result['staff_leaderboard'][0]['closed'],1)

    def test_invalid_timestamps_and_precreation_response_do_not_hide_first_reply(self):
        logs=[event(1,'staff_message',200),event(1,'created',100),event(1,'staff_message',70),
              {**event(2,'created'),'timestamp':'bad'}, {**event(3,'created'),'timestamp':float('nan')}]
        result=build_analytics(logs,[],7,now=NOW)
        self.assertEqual(result['all_time_created'],1)
        self.assertEqual(result['metrics']['reply']['average'],30)
        self.assertEqual(timestamp('2026-10-30T12:00:00'),NOW)

    def test_avatars_only_use_discord_cdn_and_duration_has_empty_state(self):
        self.assertEqual(avatar_url('1','https://cdn.discordapp.com/avatars/1/a.gif?size=128'),'https://cdn.discordapp.com/avatars/1/a.gif?size=128')
        for url in ['javascript:alert(1)','https://evil.test/avatar.png','https://cdn.discordapp.com.evil.test/x']:
            self.assertIn('/embed/avatars/',avatar_url('1',url))
        self.assertEqual(duration(None),'No data')
        self.assertEqual(duration(3600),'1.0h')

class AnalyticsRouteTests(DashboardFixture, unittest.TestCase):
    use_mongo=False
    def setUp(self):
        super().setUp()
        self.module=importlib.import_module('main')
        self.client=self.module.app.test_client()
        self.seed_access({'allowed_users':['111'],'analytics_roles':['222']})
        with self.client.session_transaction() as session:
            session['user']={'id':'111','username':'Viewer','avatar_url':'https://cdn.discordapp.com/embed/avatars/0.png'}

    def test_authorized_analytics_renders_real_charts_and_safe_reason(self):
        from utils import storage
        now = dt.datetime.now(dt.timezone.utc).timestamp()
        self.seed_logs([event(1,'created',timestamp=now-120),event(1,'closed',timestamp=now-1,executor={'id':'2','name':'Helper'},reason='<script>alert(1)</script>')])
        with patch.object(self.module,'is_admin',return_value=True),patch.object(self.module,'has_dashboard_access',return_value=True),patch.object(self.module,'has_analytics_access',return_value=True),patch.object(storage,'get_tickets_data',return_value={'active_tickets':[]}):
            response=self.client.get('/dashboard/analytics?analytics_period=14')
        self.assertEqual(response.status_code,200)
        html=response.get_data(as_text=True)
        self.assertIn('analytics.js',html)
        self.assertIn('arrival-heatmap',html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',html)
        self.assertNotIn('<script>alert(1)</script>',html)
        self.assertIn("Helper's Discord avatar",html)

    def test_home_alias_and_overview_bookmark_show_home(self):
        for path in ['/dashboard/home','/dashboard/overview']:
            with self.subTest(path=path), patch.object(self.module,'is_admin',return_value=True), patch.object(self.module,'has_dashboard_access',return_value=True), patch.object(self.module,'remember_base_url'):
                response=self.client.get(path)
            self.assertEqual(response.status_code,200)
            self.assertIn(b'<h1>Home</h1>',response.data)
            self.assertIn(b'aria-label="Home"',response.data)

    def test_analytics_role_restriction_still_applies(self):
        with patch.object(self.module,'is_admin',return_value=False),patch.object(self.module,'get_member_role_ids',return_value=[]),patch.object(self.module,'has_dashboard_access',return_value=True),patch.object(self.module,'has_analytics_access',return_value=False):
            response=self.client.get('/dashboard/analytics')
        self.assertEqual(response.status_code,403)

class TicketMessageAnalyticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_staff_owner_guest_and_bot_messages_are_classified_without_content(self):
        module=importlib.import_module('cogs.tickets')
        author=SimpleNamespace(id=1,bot=False,roles=[],display_avatar=SimpleNamespace(url='https://cdn.discordapp.com/avatars/1/test.png'))
        message=SimpleNamespace(id=9,author=author,guild=object(),channel=SimpleNamespace(id=10),created_at=dt.datetime.now(dt.timezone.utc),content='Private text')
        with patch.object(module,'get_active_ticket',return_value={'user_id':'1'}),patch.object(module,'touch_ticket_activity') as touch,patch.object(module,'get_settings',return_value={}),patch.object(module,'can_manage_ticket',return_value=False),patch.object(module,'append_ticket_log') as log:
            await module.TicketsCog.on_message(object(),message)
            self.assertEqual(log.call_args.args[0]['action'],'member_message')
            self.assertNotIn('content',log.call_args.args[0]);touch.assert_called_once()
        author.id=2
        with patch.object(module,'get_active_ticket',return_value={'user_id':'1'}),patch.object(module,'get_settings',return_value={}),patch.object(module,'can_manage_ticket',return_value=True),patch.object(module,'touch_ticket_activity') as touch,patch.object(module,'append_ticket_log') as log:
            await module.TicketsCog.on_message(object(),message)
            self.assertEqual(log.call_args.args[0]['action'],'staff_message');touch.assert_not_called()
        for bot in [False,True]:
            author.bot=bot
            with patch.object(module,'get_active_ticket',return_value={'user_id':'1'}),patch.object(module,'get_settings',return_value={}),patch.object(module,'can_manage_ticket',return_value=False),patch.object(module,'append_ticket_log') as log:
                await module.TicketsCog.on_message(object(),message)
                log.assert_not_called()
