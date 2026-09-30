"""副本匹配 - 含5个bug"""
from dataclasses import dataclass, field
import threading

@dataclass
class MatchPlayer:
    pid: str
    role: str  # tank/healer/dps
    server: str
    latency: int = 50
    in_queue: bool = False
    invites: list = field(default_factory=list)

class MatchSystem:
    def __init__(self):
        self.queue: list[MatchPlayer] = []
        self.locks: dict[str, threading.Lock] = {}  # bug1: 可能死锁

    def match_player(self, player: MatchPlayer) -> bool:
        # bug1: 按加入顺序加锁，可能死锁
        lock1 = self.locks.setdefault(player.pid, threading.Lock())
        with lock1:
            player.in_queue = True
            self.queue.append(player)
        return True

    def handle_timeout(self, player: MatchPlayer) -> None:
        # bug2: 不通知关联玩家
        if player in self.queue:
            self.queue.remove(player)
        player.in_queue = False

    def check_team_composition(self, team: list[MatchPlayer]) -> bool:
        # bug3: 只检查有没有，不检查数量
        has_tank = any(p.role == "tank" for p in team)
        has_healer = any(p.role == "healer" for p in team)
        return has_tank and has_healer

    def cross_server_match(self, players: list[MatchPlayer]) -> list:
        # bug4: 不考虑延迟
        return players

    def cancel_match(self, player: MatchPlayer) -> None:
        # bug5: 不撤回已发出的邀请
        if player in self.queue:
            self.queue.remove(player)
        player.in_queue = False
