"""
feedback_tracker.py — Hermès v7.0: Bet Result Tracker

Tracks every bet and its outcome.
Exposes rolling statistics for the learning algorithm.
Uses EMA (exponential moving average) for smoothed metrics.
Persists to feedback_log.json.
"""

import json, logging, math
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)
UTC    = timezone.utc

# ── EMA DECAY ────────────────────────────────────────────────────────────────
# α = 2/(N+1)  → N=20 bets half-life
EMA_ALPHA = 2 / (20 + 1)   # ≈ 0.0952

FEEDBACK_FILE = "feedback_log.json"


class BetRecord:
    __slots__ = ("bet_id","match","market","mk","scat","odds","stake",
                 "ev","conf","kf","ts_placed","ts_settled","result","profit")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


class FeedbackTracker:
    """
    Records picks, settles results, exposes per-market and per-sport stats.
    """

    def __init__(self):
        self._records: list[BetRecord] = []
        self._load()

    # ── PERSISTENCE ──────────────────────────────────────────────────────────

    def _load(self):
        try:
            with open(FEEDBACK_FILE) as f:
                raw = json.load(f)
            self._records = [BetRecord.from_dict(r) for r in raw]
            logger.info("FeedbackTracker: loaded %d records", len(self._records))
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.error("FeedbackTracker._load: %s", e)

    def _save(self):
        try:
            with open(FEEDBACK_FILE, "w") as f:
                json.dump([r.to_dict() for r in self._records], f, indent=2)
        except Exception as e:
            logger.error("FeedbackTracker._save: %s", e)

    # ── WRITE ────────────────────────────────────────────────────────────────

    def record_pick(self, bet_id: int, match: str, market: str,
                    mk: str, scat: str, odds: float, stake: float,
                    ev: float, conf: float, kf: float) -> None:
        rec = BetRecord(
            bet_id=bet_id, match=match, market=market,
            mk=mk, scat=scat, odds=odds, stake=stake,
            ev=ev, conf=conf, kf=kf,
            ts_placed=datetime.now(UTC).isoformat(),
            ts_settled=None, result=None, profit=None,
        )
        self._records.append(rec)
        self._save()

    def settle(self, bet_id: int, result: str) -> Optional[BetRecord]:
        """
        result: "WON" or "LOST"
        Returns updated record or None if not found.
        """
        for rec in reversed(self._records):
            if rec.bet_id == bet_id and rec.result is None:
                rec.result     = result.upper()
                rec.ts_settled = datetime.now(UTC).isoformat()
                if result.upper() == "WON":
                    rec.profit = round(rec.stake * (rec.odds - 1.0), 2)
                else:
                    rec.profit = -rec.stake
                self._save()
                return rec
        logger.warning("settle: bet_id %s not found or already settled", bet_id)
        return None

    # ── READ ─────────────────────────────────────────────────────────────────

    @property
    def settled(self) -> list:
        return [r for r in self._records if r.result is not None]

    def last_n(self, n: int) -> list:
        return self.settled[-n:]

    def by_market(self, mk: str) -> list:
        return [r for r in self.settled if r.mk == mk]

    def by_sport(self, scat: str) -> list:
        return [r for r in self.settled if r.scat == scat]

    # ── STATS ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _stats(records: list) -> dict:
        """Compute win_rate, roi, ema_wr from a list of BetRecords."""
        n = len(records)
        if n == 0:
            return dict(n=0, wins=0, losses=0, wr=0.0, roi=0.0, ema_wr=0.0,
                        total_stake=0.0, total_profit=0.0)
        wins   = sum(1 for r in records if r.result == "WON")
        losses = n - wins
        stake  = sum(r.stake  or 0 for r in records)
        profit = sum(r.profit or 0 for r in records)

        # EMA win rate (most-recent bets weighted more)
        ema_wr = 0.5
        for r in records:
            outcome = 1.0 if r.result == "WON" else 0.0
            ema_wr  = EMA_ALPHA * outcome + (1 - EMA_ALPHA) * ema_wr

        return dict(
            n=n, wins=wins, losses=losses,
            wr=wins/n if n else 0.0,
            roi=profit/stake if stake else 0.0,
            ema_wr=ema_wr,
            total_stake=stake,
            total_profit=profit,
        )

    def global_stats(self) -> dict:
        return self._stats(self.settled)

    def market_stats(self) -> dict:
        markets = set(r.mk for r in self.settled)
        return {mk: self._stats(self.by_market(mk)) for mk in markets}

    def sport_stats(self) -> dict:
        sports = set(r.scat for r in self.settled)
        return {scat: self._stats(self.by_sport(scat)) for scat in sports}

    def recent_roi(self, n: int = 10) -> float:
        recs = self.last_n(n)
        if not recs: return 0.0
        s = sum(r.stake  or 0 for r in recs)
        p = sum(r.profit or 0 for r in recs)
        return p / s if s else 0.0

    def format_stats(self) -> str:
        g = self.global_stats()
        mk = self.market_stats()
        sp = self.sport_stats()

        mk_lines = "\n".join(
            f"  {k}: {v['n']}b WR={v['wr']:.0%} ROI={v['roi']:+.1%}"
            for k, v in sorted(mk.items()) if v["n"] >= 3
        ) or "  (no data yet)"

        sp_lines = "\n".join(
            f"  {k}: {v['n']}b WR={v['wr']:.0%} ROI={v['roi']:+.1%}"
            for k, v in sorted(sp.items()) if v["n"] >= 3
        ) or "  (no data yet)"

        return (
            f"📊 *HERMÈS STATS*\n\n"
            f"Total settled: {g['n']} bets\n"
            f"WR: {g['wr']:.0%} | EMA WR: {g['ema_wr']:.0%}\n"
            f"ROI: {g['roi']:+.1%} | Profit: {g['total_profit']:+.2f} UAH\n\n"
            f"*Per market*\n{mk_lines}\n\n"
            f"*Per sport*\n{sp_lines}"
        )
