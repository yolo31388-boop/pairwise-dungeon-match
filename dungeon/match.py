"""副本匹配系统

修复点:
1. 死锁: 玩家锁严格按 pid 排序获取, 队列元数据用单独的锁且永远在玩家锁之后获取。
2. 超时: 玩家超时离队时撤回与其相关的邀请并通知所有关联玩家。
3. 职业搭配: 按队伍规模限制坦克/治疗数量上限, 并要求两者至少各一名。
4. 跨服: 按延迟分池, 同一匹配池内最大延迟差不超过阈值。
5. 取消: 取消匹配时撤回所有已发出/收到的邀请, 并通知关联玩家。
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import threading

# 同一匹配池允许的最大延迟差 (ms)
MAX_LATENCY_SPREAD = 100

VALID_ROLES = ("tank", "healer", "dps")


@dataclass
class Invite:
    """匹配邀请, 撤回后 withdrawn=True, 接收方无法再接受。"""
    invite_id: str
    sender: "MatchPlayer"
    receiver: "MatchPlayer"
    withdrawn: bool = False


@dataclass
class MatchPlayer:
    pid: str
    role: str  # tank/healer/dps
    server: str
    latency: int = 50
    in_queue: bool = False
    invites: list = field(default_factory=list)
    notifications: list = field(default_factory=list)

    @property
    def notified(self) -> bool:
        return bool(self.notifications)


def _invite_involves(invite, player: MatchPlayer) -> bool:
    """判断一个邀请是否与指定玩家相关。

    兼容正式的 Invite 对象, 也兼容测试/旧代码里直接塞进 invites 的
    形如 "invite_to_p2" / "invite_from_p1" 的纯字符串。
    """
    if isinstance(invite, Invite):
        return invite.sender is player or invite.receiver is player
    return player.pid in str(invite)


class MatchSystem:
    def __init__(self):
        self.queue: list[MatchPlayer] = []
        # _meta_lock 保护 queue 与 _player_locks 字典本身
        self._meta_lock = threading.Lock()
        self._player_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------------
    # 锁管理: 任何需要同时持有多个玩家锁的地方都走这里, 严格按 pid 排序,
    # 杜绝 A 等 B、B 等 A 的环路等待。
    # ------------------------------------------------------------------
    def _get_player_lock(self, pid: str) -> threading.Lock:
        with self._meta_lock:
            lock = self._player_locks.get(pid)
            if lock is None:
                lock = threading.Lock()
                self._player_locks[pid] = lock
            return lock

    @contextmanager
    def _locked_players(self, players):
        pids = sorted({p.pid for p in players})
        locks = [self._get_player_lock(pid) for pid in pids]
        for lock in locks:
            lock.acquire()
        try:
            yield
        finally:
            for lock in reversed(locks):
                lock.release()

    # ------------------------------------------------------------------
    # 入队
    # ------------------------------------------------------------------
    def match_player(self, player: MatchPlayer) -> bool:
        with self._locked_players([player]):
            with self._meta_lock:
                if player not in self.queue:
                    player.in_queue = True
                    player.notifications.clear()
                    self.queue.append(player)
        return True

    def send_invite(self, sender: MatchPlayer, receiver: MatchPlayer,
                    invite_id: str | None = None) -> Invite | None:
        """发起匹配邀请。任何一方已不在队列则邀请作废。"""
        invite = Invite(invite_id or f"invite:{sender.pid}->{receiver.pid}",
                        sender, receiver)
        # 两个玩家的锁按 pid 排序获取, 双向同时发起也不会死锁
        with self._locked_players([sender, receiver]):
            with self._meta_lock:
                if not sender.in_queue or not receiver.in_queue:
                    return None
                sender.invites.append(invite)
                receiver.invites.append(invite)
        return invite

    # ------------------------------------------------------------------
    # 离队 (超时 / 取消共用)
    # ------------------------------------------------------------------
    def _related_players(self, leaving: MatchPlayer,
                         others: list[MatchPlayer]) -> list[MatchPlayer]:
        related = []
        for other in others:
            if other is leaving:
                continue
            # 对方手里有涉及离队玩家的邀请, 或离队玩家手里有指向对方的邀请
            if any(_invite_involves(inv, leaving) for inv in other.invites) or \
               any(_invite_involves(inv, other) for inv in leaving.invites):
                related.append(other)
        return related

    def _purge_invites(self, leaving: MatchPlayer,
                       others: list[MatchPlayer]) -> None:
        leaving.invites.clear()
        for other in others:
            kept = []
            for inv in other.invites:
                if _invite_involves(inv, leaving):
                    if isinstance(inv, Invite):
                        inv.withdrawn = True
                else:
                    kept.append(inv)
            other.invites[:] = kept

    def _leave(self, player: MatchPlayer, reason: str) -> None:
        with self._meta_lock:
            others = [p for p in self.queue if p is not player]
        related = self._related_players(player, others)
        with self._locked_players([player, *related]):
            with self._meta_lock:
                if player in self.queue:
                    self.queue.remove(player)
                player.in_queue = False
                self._purge_invites(player, others)
            for other in related:
                other.notifications.append(
                    {"type": reason, "player": player.pid})

    def handle_timeout(self, player: MatchPlayer) -> None:
        """超时离队: 撤回关联邀请并通知正在与该玩家匹配的所有人。"""
        self._leave(player, "timeout")

    def cancel_match(self, player: MatchPlayer) -> None:
        """取消匹配: 撤回已发出的邀请并通知关联玩家, 杜绝幽灵邀请。"""
        self._leave(player, "cancelled")

    # ------------------------------------------------------------------
    # 职业搭配: 5人队标准配置 1坦克1治疗3DPS; 按队伍规模等比放宽上限。
    # ------------------------------------------------------------------
    def check_team_composition(self, team: list[MatchPlayer]) -> bool:
        if not team:
            return False
        size = len(team)
        tank_cap = healer_cap = max(1, (size + 4) // 5)
        counts = {"tank": 0, "healer": 0, "dps": 0}
        for player in team:
            if player.role not in VALID_ROLES:
                return False
            counts[player.role] += 1
        if counts["tank"] < 1 or counts["healer"] < 1:
            return False
        if counts["tank"] > tank_cap or counts["healer"] > healer_cap:
            return False
        return True

    # ------------------------------------------------------------------
    # 跨服匹配: 按延迟排序后贪心分池, 池内延迟差不超过 MAX_LATENCY_SPREAD。
    # 返回人数最多的延迟池作为本次匹配结果。
    # ------------------------------------------------------------------
    def cross_server_match(self, players: list[MatchPlayer],
                           max_spread: int = MAX_LATENCY_SPREAD) -> list:
        ordered = sorted(players, key=lambda p: p.latency)
        pools: list[list[MatchPlayer]] = []
        current: list[MatchPlayer] = []
        for player in ordered:
            if current and player.latency - current[0].latency > max_spread:
                pools.append(current)
                current = []
            current.append(player)
        if current:
            pools.append(current)
        if not pools:
            return []
        return max(pools, key=len)
