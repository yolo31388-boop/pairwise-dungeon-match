"""副本匹配系统"""
from dataclasses import dataclass, field
import threading

TEAM_SIZE = 5
TANK_COUNT = 1
HEALER_COUNT = 1
DPS_COUNT = TEAM_SIZE - TANK_COUNT - HEALER_COUNT
MAX_LATENCY_DIFF = 200  # 同池玩家之间允许的最大延迟差(ms)


@dataclass
class MatchPlayer:
    pid: str
    role: str  # tank/healer/dps
    server: str
    latency: int = 50
    in_queue: bool = False
    invites: list = field(default_factory=list)
    notified: bool = False  # 关联玩家超时/取消时置位，用于通知等待方


class MatchSystem:
    def __init__(self):
        self.queue: list[MatchPlayer] = []
        # 单一队列锁：不再对单个玩家分别加锁，避免互相匹配时循环等待导致死锁
        self._lock = threading.RLock()

    def match_player(self, player: MatchPlayer) -> bool:
        with self._lock:
            player.in_queue = True
            player.notified = False
            if player not in self.queue:
                self.queue.append(player)
        return True

    def _withdraw_invites(self, player: MatchPlayer) -> None:
        """撤回 player 与队列中其他人之间所有已发出的邀请"""
        for other in self.queue:
            if other is player:
                continue
            other.invites = [inv for inv in other.invites if player.pid not in inv]
        player.invites.clear()

    def _related_players(self, player: MatchPlayer) -> list[MatchPlayer]:
        """找出正和 player 有邀请往来、正在等待其结果的玩家"""
        related = []
        for other in self.queue:
            if other is player:
                continue
            if any(player.pid in inv for inv in other.invites) or \
               any(other.pid in inv for inv in player.invites):
                related.append(other)
        return related

    def handle_timeout(self, player: MatchPlayer) -> None:
        with self._lock:
            if player in self.queue:
                self.queue.remove(player)
            player.in_queue = False
            related = self._related_players(player)
            self._withdraw_invites(player)
            # 通知所有正在等待该玩家的关联方，避免他们继续等一个已离开的人
            for other in related:
                other.notified = True

    def check_team_composition(self, team: list[MatchPlayer]) -> bool:
        # 满编5人本：1坦克 + 1治疗 + 3输出，检查各职业的确切数量而非仅"有没有"
        if len(team) != TEAM_SIZE:
            return False
        tanks = sum(1 for p in team if p.role == "tank")
        healers = sum(1 for p in team if p.role == "healer")
        dps = sum(1 for p in team if p.role == "dps")
        return tanks == TANK_COUNT and healers == HEALER_COUNT and dps == DPS_COUNT

    def cross_server_match(self, players: list[MatchPlayer]) -> list:
        # 按延迟分池：滑动窗口找出彼此延迟差不超过阈值的最大可匹配池，
        # 高延迟(如外服)玩家不会被塞进低延迟(如国服)玩家的池子
        if not players:
            return []
        ordered = sorted(players, key=lambda p: p.latency)
        best: list[MatchPlayer] = []
        start = 0
        for end in range(len(ordered)):
            while ordered[end].latency - ordered[start].latency > MAX_LATENCY_DIFF:
                start += 1
            window = ordered[start:end + 1]
            if len(window) > len(best):
                best = window
        return best

    def cancel_match(self, player: MatchPlayer) -> None:
        with self._lock:
            if player in self.queue:
                self.queue.remove(player)
            player.in_queue = False
            # 撤回已发出的邀请，防止取消后仍能收到并接受邀请
            self._withdraw_invites(player)
