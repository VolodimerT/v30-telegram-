"""scheduler.py — Autonomous scheduling with APScheduler (Phase 7)."""
from __future__ import annotations
import os
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

UTC = timezone.utc
logger = logging.getLogger("scheduler")


class AutonomousScheduler:
    """Manage autonomous bot execution without manual commands."""
    
    def __init__(self, pipeline_runner, telegram_notifier):
        """
        Args:
            pipeline_runner: callable that runs run_auto_pipeline / run_live_pipeline
            telegram_notifier: callable(message: str) for notifications
        """
        self.scheduler = AsyncIOScheduler()
        self.pipeline = pipeline_runner
        self.notify = telegram_notifier
        self.is_running = False
    
    def start(self) -> None:
        """Start the autonomous scheduler."""
        if self.is_running:
            logger.info("Scheduler already running")
            return
        
        # Morning scan: 08:00 UTC
        self.scheduler.add_job(
            self._morning_scan,
            trigger=CronTrigger(hour=8, minute=0),
            id="morning_scan",
            name="Morning scan (EPL, LaLiga, SerieA)"
        )
        
        # Lunch scan: 12:00 UTC
        self.scheduler.add_job(
            self._lunch_scan,
            trigger=CronTrigger(hour=12, minute=0),
            id="lunch_scan",
            name="Lunch scan (EPL, LaLiga, SerieA, NBA)"
        )
        
        # Evening scan: 18:00 UTC
        self.scheduler.add_job(
            self._evening_scan,
            trigger=CronTrigger(hour=18, minute=0),
            id="evening_scan",
            name="Evening scan + watchlist"
        )
        
        # Live mode: every 30 min
        self.scheduler.add_job(
            self._live_scan,
            trigger=CronTrigger(minute="*/30"),
            id="live_scan",
            name="Live in-play scan (tight gates)"
        )
        
        # Weekly stats: Monday 20:00 UTC
        self.scheduler.add_job(
            self._weekly_stats,
            trigger=CronTrigger(day_of_week=0, hour=20, minute=0),
            id="weekly_stats",
            name="Weekly stats report"
        )
        
        self.scheduler.start()
        self.is_running = True
        logger.info("Autonomous scheduler started")
    
    def stop(self) -> None:
        """Stop the autonomous scheduler."""
        if not self.is_running:
            return
        self.scheduler.shutdown(wait=False)
        self.is_running = False
        logger.info("Autonomous scheduler stopped")
    
    async def _morning_scan(self) -> None:
        """Scan major European leagues in the morning."""
        logger.info("🌅 MORNING SCAN TRIGGERED")
        try:
            summary = self.pipeline("AUTO today epl seriea laliga strict")
            accepted = [x for x in summary.get("results", []) if x["decision"] != "PASS"]
            if accepted:
                msg = f"🌅 MORNING SCAN\n✅ Found {len(accepted)} picks\n\nTop 3:"
                for item in accepted[:3]:
                    msg += (
                        f"\n- {item['match']} | {item['decision']} {item['selection']}"
                        f" | odds {item['best_odds']} | stake {item['stake']}"
                    )
                await self.notify(msg)
            else:
                await self.notify("🌅 MORNING SCAN\n🔕 All PASS today")
        except Exception as e:
            logger.error(f"Morning scan error: {e}")
            await self.notify(f"❌ Morning scan failed: {e}")
    
    async def _lunch_scan(self) -> None:
        """Scan at midday including US leagues."""
        logger.info("☀️ LUNCH SCAN TRIGGERED")
        try:
            summary = self.pipeline("AUTO today epl seriea laliga nba strict")
            accepted = [x for x in summary.get("results", []) if x["decision"] != "PASS"]
            if accepted:
                msg = f"☀️ LUNCH SCAN\n✅ Found {len(accepted)} picks"
                for item in accepted[:3]:
                    msg += (
                        f"\n- {item['match']} | {item['decision']} {item['selection']}"
                        f" | {item['best_odds']} | ${item['stake']}"
                    )
                await self.notify(msg)
        except Exception as e:
            logger.error(f"Lunch scan error: {e}")
    
    async def _evening_scan(self) -> None:
        """Evening scan with watchlist focus."""
        logger.info("🌆 EVENING SCAN TRIGGERED")
        try:
            from pipeline import build_scanwatch_request
            req = build_scanwatch_request()
            if req:
                summary = self.pipeline(req)
                accepted = [x for x in summary.get("results", []) if x["decision"] != "PASS"]
                if accepted:
                    msg = f"🌆 WATCHLIST SCAN\n✅ Found {len(accepted)} picks"
                    for item in accepted[:5]:
                        msg += (f"\n- {item['match']} | {item['selection']} | "
                               f"{item['best_odds']} | ${item['stake']}")
                    await self.notify(msg)
        except Exception as e:
            logger.error(f"Evening scan error: {e}")
    
    async def _live_scan(self) -> None:
        """Scan in-play markets every 30 minutes."""
        logger.info("🔴 LIVE SCAN TRIGGERED")
        try:
            summary = self.pipeline("LIVE epl nba")
            accepted = [x for x in summary.get("results", []) if x["decision"] != "PASS"]
            if accepted:
                msg = f"🔴 LIVE PICKS\n✅ {len(accepted)} in-play bets found"
                for item in accepted[:3]:
                    msg += (
                        f"\n- {item['match']} | {item['selection']}"
                        f" | odds {item['best_odds']} | ${item['stake']}"
                    )
                await self.notify(msg)
        except Exception as e:
            logger.error(f"Live scan error: {e}")
    
    async def _weekly_stats(self) -> None:
        """Send weekly performance report."""
        logger.info("📊 WEEKLY STATS TRIGGERED")
        try:
            from brm import format_stats_report_compact
            stats = format_stats_report_compact()
            await self.notify(f"📊 WEEKLY REPORT\n\n{stats}")
        except Exception as e:
            logger.error(f"Weekly stats error: {e}")
    
    def add_custom_job(self, schedule: str, pipeline_cmd: str, job_id: str) -> bool:
        """Add custom autonomous job."""
        try:
            parts = schedule.split()
            if len(parts) != 5:
                return False
            
            minute, hour, day, month, dow = parts
            self.scheduler.add_job(
                self._run_custom,
                trigger=CronTrigger(minute=minute, hour=hour, day=day, 
                                   month=month, day_of_week=dow),
                args=(pipeline_cmd,),
                id=job_id,
                name=f"Custom: {pipeline_cmd[:30]}"
            )
            logger.info(f"Added custom job: {job_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to add custom job: {e}")
            return False
    
    async def _run_custom(self, cmd: str) -> None:
        """Execute custom pipeline command."""
        try:
            summary = self.pipeline(cmd)
            accepted = [x for x in summary.get("results", []) if x["decision"] != "PASS"]
            if accepted:
                msg = f"🤖 AUTO: {cmd[:50]}\n✅ {len(accepted)} picks"
                for item in accepted[:3]:
                    msg += (f"\n- {item['match']} | {item['selection']} | "
                           f"{item['best_odds']}")
                await self.notify(msg)
        except Exception as e:
            await self.notify(f"❌ Auto job failed: {e}")


class TimeWindowFilter:
    """Filter picks based on match timing for autonomous execution."""
    
    def __init__(self, min_hours: float = 2.0, max_hours: float = 72.0):
        """Filter picks by time to match start."""
        self.min_hours = min_hours
        self.max_hours = max_hours
    
    def filter_results(self, results: list) -> list:
        """Keep only picks within optimal timing window."""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        filtered = []
        
        for item in results:
            try:
                commence = datetime.fromisoformat(
                    item["commence_time"].replace("Z", "+00:00")
                )
                hours_to_start = (commence - now).total_seconds() / 3600.0
                
                if self.min_hours <= hours_to_start <= self.max_hours:
                    filtered.append(item)
            except Exception:
                pass
        
        return filtered


class AdaptiveThresholds:
    """Adjust thresholds based on recent performance."""
    
    def __init__(self, history_path: str):
        """Load pick history for adaptive analysis."""
        self.history_path = history_path
        self.roi_window = 30
    
    def get_adjusted_min_ev(self, base_min_ev: float) -> float:
        """Increase EV threshold if recent ROI is negative."""
        try:
            from brm import load_pick_history, summarize_history
            history = load_pick_history()
            meta = summarize_history(history[-self.roi_window:])
            
            roi = meta.get("roi", 0.0)
            if roi < -10.0:
                return base_min_ev + 2.0
            if roi < -5.0:
                return base_min_ev + 1.0
            if roi > 20.0:
                return max(base_min_ev - 1.0, 3.0)
            
            return base_min_ev
        except Exception:
            return base_min_ev
    
    def should_pause_execution(self) -> bool:
        """Pause autonomous betting if losing streak detected."""
        try:
            from brm import load_pick_history
            history = load_pick_history()
            settled = [x for x in history[-10:] if x.get("status") == "SETTLED"]
            
            if len(settled) >= 5:
                recent_losses = sum(1 for x in settled[-5:] 
                                   if x.get("settled_result") == "LOSS")
                if recent_losses == 5:
                    return True
            
            return False
        except Exception:
            return False
