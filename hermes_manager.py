"""hermes_manager.py — Hermès v7.0: Dynamic Parameter Manager"""
import json, logging, os, copy
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)
UTC    = timezone.utc

DEFAULT_STATE = {
    "schema_version": "7.0",
    "last_updated": None,
    "total_bets": 0,
    "confidence": {
        "h2h": 0.62, "spreads": 0.60, "totals": 0.57,
        "btts": 0.63, "draw_no_bet": 0.61,
        "h2h_h1": 0.60, "totals_h1": 0.57,
        "h2h_p1": 0.59, "h2h_p2": 0.58, "h2h_p3": 0.57,
    },
    "kelly_multiplier": 1.0,
    "ev_accept": 0.15, "ev_consider": 0.06, "min_ev": 0.02, "min_kelly": 0.003,
    "min_odds": 1.60, "max_odds": 6.00,
    "sport_boost": {
        "soccer": 1.00, "basketball": 0.97, "icehockey": 0.97,
        "tennis": 1.03, "mma": 0.95, "boxing": 0.95, "baseball": 0.98,
    },
    "stop_loss_triggered": False,
    "initial_bank": 1019.0, "peak_bank": 1019.0,
    "stop_loss_pct": 0.15, "stop_loss_resume_pct": 0.07,
    "cycles_completed": 0, "last_deep_analysis": None,
    "total_bets_at_last_cycle": 0, "total_bets_at_last_deep": 0,
    "changelog": [],
}

CONF_MIN = 0.45; CONF_MAX = 0.85; CONF_STEP = 0.02
KELLY_MIN = 0.50; KELLY_MAX = 2.00; KELLY_STEP = 0.10
MIN_SAMPLE = 15


class HermesManager:
    STATE_FILE = "hermes_state.json"

    def __init__(self):
        self.state = self._load()

    def _load(self):
        try:
            with open(self.STATE_FILE) as f:
                raw = json.load(f)
            merged = copy.deepcopy(DEFAULT_STATE)
            for k, v in raw.items():
                if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
                    merged[k].update(v)
                else:
                    merged[k] = v
            return merged
        except FileNotFoundError:
            return copy.deepcopy(DEFAULT_STATE)
        except Exception as e:
            logger.error("HermesManager._load: %s", e)
            return copy.deepcopy(DEFAULT_STATE)

    def save(self):
        try:
            self.state["last_updated"] = datetime.now(UTC).isoformat()
            with open(self.STATE_FILE, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.error("HermesManager.save: %s", e)

    def get_confidence(self, mk, scat="default"):
        base  = self.state["confidence"].get(mk, 0.58)
        boost = self.state["sport_boost"].get(scat, 1.0)
        return min(CONF_MAX, max(CONF_MIN, base * boost))

    def get_kelly_fraction(self):
        return 0.25 * self.state["kelly_multiplier"]

    def get_ev_accept(self):   return self.state["ev_accept"]
    def get_ev_consider(self): return self.state["ev_consider"]
    def get_min_ev(self):      return self.state["min_ev"]
    def get_min_kelly(self):   return self.state["min_kelly"]
    def get_min_odds(self):    return self.state["min_odds"]
    def get_max_odds(self):    return self.state["max_odds"]
    def is_paused(self):       return self.state["stop_loss_triggered"]

    def update_bank(self, new_bank):
        # Only update peak when not in drawdown lockout
        if not self.state["stop_loss_triggered"]:
            if new_bank > self.state["peak_bank"]:
                self.state["peak_bank"] = new_bank
        drawdown = (self.state["peak_bank"] - new_bank) / self.state["peak_bank"]
        if not self.state["stop_loss_triggered"]:
            if drawdown >= self.state["stop_loss_pct"]:
                self.state["stop_loss_triggered"] = True
                logger.warning("STOP-LOSS triggered! Bank=%.2f Peak=%.2f DD=%.1f%%",
                               new_bank, self.state["peak_bank"], drawdown * 100)
                self._log_change("STOP_LOSS", f"DD={drawdown:.1%} bank={new_bank:.2f}")
        else:
            resume = self.state["stop_loss_pct"] - self.state["stop_loss_resume_pct"]
            if drawdown <= resume:
                self.state["stop_loss_triggered"] = False
                logger.info("Stop-loss cleared — resuming")
                self._log_change("STOP_LOSS_CLEAR", f"bank={new_bank:.2f}")
        self.save()

    def adjust_confidence(self, mk, delta, reason=""):
        delta   = max(-CONF_STEP, min(CONF_STEP, delta))
        current = self.state["confidence"].get(mk, 0.58)
        new_val = max(CONF_MIN, min(CONF_MAX, current + delta))
        if abs(new_val - current) > 1e-6:
            self.state["confidence"][mk] = round(new_val, 4)
            self._log_change(f"CONF_{mk}", f"{current:.3f}→{new_val:.3f} {reason}")

    def adjust_kelly(self, delta, reason=""):
        delta   = max(-KELLY_STEP, min(KELLY_STEP, delta))
        current = self.state["kelly_multiplier"]
        new_val = max(KELLY_MIN, min(KELLY_MAX, current + delta))
        if abs(new_val - current) > 1e-6:
            self.state["kelly_multiplier"] = round(new_val, 3)
            self._log_change("KELLY", f"{current:.3f}→{new_val:.3f} {reason}")

    def adjust_sport_boost(self, scat, delta, reason=""):
        current = self.state["sport_boost"].get(scat, 1.0)
        new_val = max(0.80, min(1.20, current + delta))
        self.state["sport_boost"][scat] = round(new_val, 3)
        self._log_change(f"BOOST_{scat}", f"{current:.3f}→{new_val:.3f} {reason}")

    def increment_cycle(self):
        self.state["cycles_completed"] += 1

    def set_deep_analysis_ts(self):
        self.state["last_deep_analysis"] = datetime.now(UTC).isoformat()

    def snapshot(self):
        return {
            "schema":      self.state["schema_version"],
            "cycles":      self.state["cycles_completed"],
            "paused":      self.state["stop_loss_triggered"],
            "kelly_mul":   self.state["kelly_multiplier"],
            "confidence":  dict(self.state["confidence"]),
            "sport_boost": dict(self.state["sport_boost"]),
            "ev_accept":   self.state["ev_accept"],
            "min_ev":      self.state["min_ev"],
            "odds_range":  [self.state["min_odds"], self.state["max_odds"]],
            "last_updated": self.state.get("last_updated", "—"),
        }

    def format_status(self):
        s = self.snapshot()
        conf_lines  = "\n".join(f"  {k}: {v:.0%}"  for k, v in sorted(s["confidence"].items()))
        boost_lines = "\n".join(f"  {k}: ×{v:.2f}" for k, v in sorted(s["sport_boost"].items()))
        pause = "🔴 PAUSED" if s["paused"] else "🟢 ACTIVE"
        return (
            f"🧠 *HERMÈS STATUS* v7.0\n\n"
            f"State: {pause}\n"
            f"Cycles: {s['cycles']} | Kelly ×{s['kelly_mul']:.2f}\n"
            f"EV accept: ≥{s['ev_accept']:.0%} | Min EV: ≥{s['min_ev']:.2%}\n"
            f"Odds: {s['odds_range'][0]}–{s['odds_range'][1]}\n\n"
            f"*Confidence*\n{conf_lines}\n\n"
            f"*Sport boost*\n{boost_lines}\n\n"
            f"Updated: {s['last_updated']}"
        )

    def _log_change(self, key, msg):
        entry = {"ts": datetime.now(UTC).isoformat(), "key": key, "msg": msg}
        log = self.state.setdefault("changelog", [])
        log.append(entry)
        if len(log) > 200:
            self.state["changelog"] = log[-200:]
