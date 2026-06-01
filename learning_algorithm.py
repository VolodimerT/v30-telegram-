"""learning_algorithm.py — Hermès v7.0: Adaptive Learning Engine"""
import logging
from feedback_tracker import FeedbackTracker
from hermes_manager   import HermesManager

logger = logging.getLogger(__name__)

MIN_SAMPLE    = 15
CYCLE_EVERY_N = 10
DEEP_CYCLE_N  = 50
WR_TARGET     = 0.52
WR_BAND       = 0.03
WR_STRONG_ABOVE = 0.60
WR_STRONG_BELOW = 0.44
ROI_BOOST  =  0.10;  ROI_CUT    = -0.05
ROI_STRONG_BOOST =  0.20;  ROI_STRONG_CUT   = -0.12


class LearningAlgorithm:
    def __init__(self, manager: HermesManager, tracker: FeedbackTracker):
        self.mgr = manager
        self.trk = tracker
        self._last_cycle_at = manager.state.get("total_bets_at_last_cycle", 0)

    def run_cycle(self, bank):
        settled_n = len(self.trk.settled)
        changes   = []
        self.mgr.update_bank(bank)
        if self.mgr.is_paused():
            return ["⛔ Stop-loss active — no parameter changes"]
        new_since = settled_n - self._last_cycle_at
        if new_since >= CYCLE_EVERY_N:
            changes += self._mini_cycle()
            self._last_cycle_at = settled_n
            self.mgr.state["total_bets_at_last_cycle"] = settled_n
            self.mgr.increment_cycle()
        last_deep = self.mgr.state.get("total_bets_at_last_deep", 0)
        if settled_n - last_deep >= DEEP_CYCLE_N:
            changes += self._deep_cycle()
            self.mgr.state["total_bets_at_last_deep"] = settled_n
            self.mgr.set_deep_analysis_ts()
        if changes:
            self.mgr.save()
        return changes

    def _mini_cycle(self):
        changes = []
        roi10 = self.trk.recent_roi(10)
        if   roi10 >= ROI_STRONG_BOOST: delta, reason = +0.10, f"ROI10={roi10:+.1%} strong"
        elif roi10 >= ROI_BOOST:        delta, reason = +0.05, f"ROI10={roi10:+.1%} boost"
        elif roi10 <= ROI_STRONG_CUT:   delta, reason = -0.10, f"ROI10={roi10:+.1%} strong cut"
        elif roi10 <= ROI_CUT:          delta, reason = -0.05, f"ROI10={roi10:+.1%} cut"
        else:                           delta, reason = 0.0, ""
        if delta:
            self.mgr.adjust_kelly(delta, reason)
            changes.append(f"Kelly ×{self.mgr.state['kelly_multiplier']:.2f} ({reason})")
        for mk, s in self.trk.market_stats().items():
            if s["n"] < MIN_SAMPLE: continue
            ema = s["ema_wr"]
            if   ema >= WR_STRONG_ABOVE:       d, note = +0.02, f"EMA={ema:.0%}↑"
            elif ema > WR_TARGET + WR_BAND:    d, note = +0.01, f"EMA={ema:.0%}↑mild"
            elif ema <= WR_STRONG_BELOW:       d, note = -0.02, f"EMA={ema:.0%}↓"
            elif ema < WR_TARGET - WR_BAND:    d, note = -0.01, f"EMA={ema:.0%}↓mild"
            else:                              d, note = 0.0, ""
            if d:
                self.mgr.adjust_confidence(mk, d, note)
                changes.append(f"Conf[{mk}]→{self.mgr.state['confidence'].get(mk,0):.0%}")
        return changes

    def _deep_cycle(self):
        changes = []
        for scat, s in self.trk.sport_stats().items():
            if s["n"] < MIN_SAMPLE: continue
            roi = s["roi"]
            d = (+0.02 if roi>=0.20 else +0.01 if roi>=0.10
                 else -0.02 if roi<=-0.10 else -0.01 if roi<=0.0 else 0.0)
            if d:
                self.mgr.adjust_sport_boost(scat, d, f"ROI={roi:+.1%}")
                changes.append(f"Boost[{scat}]→×{self.mgr.state['sport_boost'].get(scat,1):.2f}")
        gs = self.trk.global_stats()
        if gs["n"] >= MIN_SAMPLE:
            if gs["roi"] < -0.05:
                old = self.mgr.state["ev_accept"]
                self.mgr.state["ev_accept"] = round(min(0.25, old+0.01), 3)
                changes.append(f"EV_accept {old:.2f}→{self.mgr.state['ev_accept']:.2f}")
            elif gs["roi"] > 0.15 and self.mgr.state["ev_accept"] > 0.12:
                old = self.mgr.state["ev_accept"]
                self.mgr.state["ev_accept"] = round(max(0.12, old-0.01), 3)
                changes.append(f"EV_accept {old:.2f}→{self.mgr.state['ev_accept']:.2f}")
        return changes
