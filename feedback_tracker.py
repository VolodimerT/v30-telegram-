"""feedback_tracker.py — Hermès v7.0: Bet Result Tracker"""
import json, logging
from datetime import datetime, timezone
from typing import Optional

logger   = logging.getLogger(__name__)
UTC      = timezone.utc
EMA_ALPHA = 2 / (20 + 1)
FEEDBACK_FILE = "feedback_log.json"


class BetRecord:
    __slots__ = ("bet_id","match","market","mk","scat","odds","stake",
                 "ev","conf","kf","ts_placed","ts_settled","result","profit")
    def __init__(self, **kw):
        for k in self.__slots__: setattr(self, k, kw.get(k))
    def to_dict(self): return {k: getattr(self, k) for k in self.__slots__}
    @classmethod
    def from_dict(cls, d): return cls(**d)


class FeedbackTracker:
    def __init__(self):
        self._records = []
        self._load()

    def _load(self):
        try:
            with open(FEEDBACK_FILE) as f:
                self._records = [BetRecord.from_dict(r) for r in json.load(f)]
        except FileNotFoundError: pass
        except Exception as e: logger.error("FeedbackTracker._load: %s", e)

    def _save(self):
        try:
            with open(FEEDBACK_FILE, "w") as f:
                json.dump([r.to_dict() for r in self._records], f, indent=2)
        except Exception as e: logger.error("FeedbackTracker._save: %s", e)

    def record_pick(self, bet_id, match, market, mk, scat, odds, stake, ev, conf, kf):
        self._records.append(BetRecord(
            bet_id=bet_id, match=match, market=market, mk=mk, scat=scat,
            odds=odds, stake=stake, ev=ev, conf=conf, kf=kf,
            ts_placed=datetime.now(UTC).isoformat(),
            ts_settled=None, result=None, profit=None))
        self._save()

    def settle(self, bet_id, result):
        for rec in reversed(self._records):
            if rec.bet_id == bet_id and rec.result is None:
                rec.result     = result.upper()
                rec.ts_settled = datetime.now(UTC).isoformat()
                rec.profit     = round(rec.stake*(rec.odds-1.0), 2) if result.upper()=="WON" else -rec.stake
                self._save()
                return rec
        return None

    @property
    def settled(self): return [r for r in self._records if r.result is not None]
    def last_n(self, n): return self.settled[-n:]
    def by_market(self, mk): return [r for r in self.settled if r.mk == mk]
    def by_sport(self, scat): return [r for r in self.settled if r.scat == scat]

    @staticmethod
    def _stats(records):
        n = len(records)
        if n == 0:
            return dict(n=0,wins=0,losses=0,wr=0.0,roi=0.0,ema_wr=0.0,
                        total_stake=0.0,total_profit=0.0)
        wins   = sum(1 for r in records if r.result=="WON")
        stake  = sum(r.stake  or 0 for r in records)
        profit = sum(r.profit or 0 for r in records)
        ema_wr = 0.5
        for r in records:
            ema_wr = EMA_ALPHA*(1.0 if r.result=="WON" else 0.0)+(1-EMA_ALPHA)*ema_wr
        return dict(n=n, wins=wins, losses=n-wins, wr=wins/n,
                    roi=profit/stake if stake else 0.0,
                    ema_wr=ema_wr, total_stake=stake, total_profit=profit)

    def global_stats(self): return self._stats(self.settled)
    def market_stats(self):
        return {mk: self._stats(self.by_market(mk)) for mk in set(r.mk for r in self.settled)}
    def sport_stats(self):
        return {s: self._stats(self.by_sport(s)) for s in set(r.scat for r in self.settled)}
    def recent_roi(self, n=10):
        recs = self.last_n(n)
        s = sum(r.stake or 0 for r in recs)
        p = sum(r.profit or 0 for r in recs)
        return p/s if s else 0.0

    def format_stats(self):
        g  = self.global_stats()
        mk = self.market_stats()
        sp = self.sport_stats()
        mk_lines = "\n".join(
            f"  {k}: {v['n']}b WR={v['wr']:.0%} ROI={v['roi']:+.1%}"
            for k,v in sorted(mk.items()) if v["n"]>=3) or "  (no data)"
        sp_lines = "\n".join(
            f"  {k}: {v['n']}b WR={v['wr']:.0%} ROI={v['roi']:+.1%}"
            for k,v in sorted(sp.items()) if v["n"]>=3) or "  (no data)"
        return (f"📊 *HERMÈS STATS*\n\nTotal: {g['n']} bets\n"
                f"WR: {g['wr']:.0%} EMA: {g['ema_wr']:.0%} ROI: {g['roi']:+.1%}\n"
                f"Profit: {g['total_profit']:+.2f} UAH\n\n"
                f"*Per market*\n{mk_lines}\n\n*Per sport*\n{sp_lines}")
