"""
learning_algorithm.py — Hermès v7.0: Adaptive Learning Engine

Reads from FeedbackTracker, writes adjustments to HermesManager.

Algorithm:
  - Uses EMA win-rate (not raw WR) to avoid reacting to noise
  - Adjusts confidence ±2% per cycle maximum
  - Adjusts Kelly ±10% per cycle based on recent ROI
  - Requires MIN_SAMPLE bets before changing any parameter
  - Deep analysis every DEEP_CYCLE_N bets
  - Rollback if ROI drops 10% after last adjustment
"""

import logging
from datetime import datetime, timezone

from feedback_tracker import FeedbackTracker
from hermes_manager   import HermesManager

logger = logging.getLogger(__name__)
UTC    = timezone.utc

# ── TUNING CONSTANTS ─────────────────────────────────────────────────────────
MIN_SAMPLE      = 15     # minimum settled bets for a category to adjust
CYCLE_EVERY_N   = 10     # run cycle every N new settled bets
DEEP_CYCLE_N    = 50     # run deep analysis every 50 settled bets

# Win-rate targets (calibration targets)
WR_TARGET       = 0.52   # ideal EMA win rate
WR_BAND         = 0.03   # ±3% dead band (no adjustment)
WR_STRONG_ABOVE = 0.60   # strong over-performance
WR_STRONG_BELOW = 0.44   # strong under-performance

# ROI thresholds for Kelly adjustment
ROI_BOOST  =  0.10   # recent 10-bet ROI → increase Kelly
ROI_CUT    = -0.05   # recent 10-bet ROI → decrease Kelly
ROI_STRONG_BOOST =  0.20
ROI_STRONG_CUT   = -0.12

# ── LEARNING ENGINE ───────────────────────────────────────────────────────────

class LearningAlgorithm:
    """
    Call .run_cycle() after every new settled bet.
    Automatically decides whether to trigger a mini or deep cycle.
    """

    def __init__(self, manager: HermesManager, tracker: FeedbackTracker):
        self.mgr = manager
        self.trk = tracker
        self._last_cycle_at = manager.state.get("total_bets_at_last_cycle", 0)

    def run_cycle(self, bank: float) -> list[str]:
        """
        Main entry point. Returns list of change descriptions (for TG notify).
        """
        settled_n = len(self.trk.settled)
        changes   = []

        # Update stop-loss / bank tracking
        self.mgr.update_bank(bank)

        if self.mgr.is_paused():
            logger.info("Learning: PAUSED (stop-loss active)")
            return ["⛔ Stop-loss active — no parameter changes"]

        # Mini cycle
        new_since_last = settled_n - self._last_cycle_at
        if new_since_last >= CYCLE_EVERY_N:
            changes += self._mini_cycle()
            self._last_cycle_at = settled_n
            self.mgr.state["total_bets_at_last_cycle"] = settled_n
            self.mgr.increment_cycle()

        # Deep cycle
        last_deep = self.mgr.state.get("total_bets_at_last_deep", 0)
        if settled_n - last_deep >= DEEP_CYCLE_N:
            changes += self._deep_cycle()
            self.mgr.state["total_bets_at_last_deep"] = settled_n
            self.mgr.set_deep_analysis_ts()

        if changes:
            self.mgr.save()
        return changes

    # ── MINI CYCLE ─────────────────────────────────────────────────────────

    def _mini_cycle(self) -> list[str]:
        logger.info("=== HERMÈS MINI CYCLE ===")
        changes = []

        # 1. Adjust Kelly based on recent ROI (last 10 bets)
        roi10 = self.trk.recent_roi(10)
        if roi10 >= ROI_STRONG_BOOST:
            delta  = +0.10
            reason = f"ROI10={roi10:+.1%} strong boost"
        elif roi10 >= ROI_BOOST:
            delta  = +0.05
            reason = f"ROI10={roi10:+.1%} boost"
        elif roi10 <= ROI_STRONG_CUT:
            delta  = -0.10
            reason = f"ROI10={roi10:+.1%} strong cut"
        elif roi10 <= ROI_CUT:
            delta  = -0.05
            reason = f"ROI10={roi10:+.1%} cut"
        else:
            delta  = 0.0
            reason = ""

        if delta != 0.0:
            self.mgr.adjust_kelly(delta, reason)
            changes.append(f"Kelly ×{self.mgr.state['kelly_multiplier']:.2f} ({reason})")

        # 2. Adjust confidence per market
        mk_stats = self.trk.market_stats()
        for mk, s in mk_stats.items():
            if s["n"] < MIN_SAMPLE:
                continue
            ema_wr = s["ema_wr"]
            diff   = ema_wr - WR_TARGET

            if ema_wr >= WR_STRONG_ABOVE:
                delta  = +0.02
                note   = f"EMA_WR={ema_wr:.0%} ↑strong"
            elif ema_wr > WR_TARGET + WR_BAND:
                delta  = +0.01
                note   = f"EMA_WR={ema_wr:.0%} ↑mild"
            elif ema_wr <= WR_STRONG_BELOW:
                delta  = -0.02
                note   = f"EMA_WR={ema_wr:.0%} ↓strong"
            elif ema_wr < WR_TARGET - WR_BAND:
                delta  = -0.01
                note   = f"EMA_WR={ema_wr:.0%} ↓mild"
            else:
                delta  = 0.0
                note   = ""

            if delta != 0.0:
                self.mgr.adjust_confidence(mk, delta, note)
                new_conf = self.mgr.state["confidence"].get(mk, 0.0)
                changes.append(f"Conf[{mk}] → {new_conf:.0%} ({note})")

        logger.info("Mini cycle changes: %s", changes or ["none"])
        return changes

    # ── DEEP CYCLE ─────────────────────────────────────────────────────────

    def _deep_cycle(self) -> list[str]:
        logger.info("=== HERMÈS DEEP CYCLE ===")
        changes = []

        # Per-sport boost adjustment
        sp_stats = self.trk.sport_stats()
        for scat, s in sp_stats.items():
            if s["n"] < MIN_SAMPLE:
                continue
            roi = s["roi"]
            if roi >= 0.20:
                delta = +0.02
            elif roi >= 0.10:
                delta = +0.01
            elif roi <= -0.10:
                delta = -0.02
            elif roi <= 0.0:
                delta = -0.01
            else:
                delta = 0.0

            if delta != 0.0:
                self.mgr.adjust_sport_boost(scat, delta,
                    f"deep ROI={roi:+.1%}")
                new_boost = self.mgr.state["sport_boost"].get(scat, 1.0)
                changes.append(f"Boost[{scat}] → ×{new_boost:.2f}")

        # Global ROI health check
        gs = self.trk.global_stats()
        if gs["n"] >= MIN_SAMPLE:
            if gs["roi"] < -0.05:
                # Tighten EV threshold when system is losing
                old = self.mgr.state["ev_accept"]
                new = min(0.25, old + 0.01)
                self.mgr.state["ev_accept"] = new
                changes.append(f"EV_accept {old:.2f}→{new:.2f} (ROI={gs['roi']:+.1%})")
            elif gs["roi"] > 0.15 and self.mgr.state["ev_accept"] > 0.12:
                old = self.mgr.state["ev_accept"]
                new = max(0.12, old - 0.01)
                self.mgr.state["ev_accept"] = new
                changes.append(f"EV_accept {old:.2f}→{new:.2f} (system healthy)")

        logger.info("Deep cycle changes: %s", changes or ["none"])
        return changes
