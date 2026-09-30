"""副本匹配 - 红态测试"""
import pytest, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dungeon.match import MatchSystem, MatchPlayer

class TestDeadlockAvoidance:
    def test_match_does_not_deadlock(self):
        ms = MatchSystem()
        p1 = MatchPlayer("p1", "tank", "s1")
        p2 = MatchPlayer("p2", "healer", "s1")
        # 同时匹配不应该死锁
        ms.match_player(p1)
        ms.match_player(p2)
        assert p1.in_queue and p2.in_queue

class TestTimeoutNotifiesRelated:
    def test_timeout_notifies_related_players(self):
        ms = MatchSystem()
        p1 = MatchPlayer("p1", "tank", "s1")
        p2 = MatchPlayer("p2", "healer", "s1")
        ms.match_player(p1)
        ms.match_player(p2)
        p1.invites.append("invite_to_p2")
        ms.handle_timeout(p1)
        # p2应该收到通知
        assert p2.in_queue == False or hasattr(p2, "notified")

class TestTeamCompositionCount:
    def test_too_many_tanks_rejected(self):
        ms = MatchSystem()
        team = [MatchPlayer(f"p{i}", "tank", "s1") for i in range(4)]
        team.append(MatchPlayer("p5", "healer", "s1"))
        assert ms.check_team_composition(team) == False

class TestCrossServerLatency:
    def test_high_latency_players_not_matched(self):
        ms = MatchSystem()
        p1 = MatchPlayer("p1", "tank", "cn", latency=50)
        p2 = MatchPlayer("p2", "healer", "us", latency=500)
        result = ms.cross_server_match([p1, p2])
        # 延迟差距过大不应该匹配
        assert len(result) <= 1

class TestCancelWithdrawsInvites:
    def test_cancel_removes_pending_invites(self):
        ms = MatchSystem()
        p1 = MatchPlayer("p1", "tank", "s1")
        p2 = MatchPlayer("p2", "healer", "s1")
        ms.match_player(p1)
        ms.match_player(p2)
        p1.invites.append("invite_to_p2")
        p2.invites.append("invite_from_p1")
        ms.cancel_match(p1)
        # p2的邀请应该被撤回
        assert len(p2.invites) == 0
