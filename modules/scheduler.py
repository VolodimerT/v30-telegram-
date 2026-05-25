import logging

logger = logging.getLogger('scanner')

class SchedulerManager:
    def __init__(self, config, odds_api, perplexity, smart_filter, telegram_bot=None):
        self.config = config
    
    def start(self):
        logger.info("✅ Scheduler started")
    
    def stop(self):
        logger.info("⛔ Scheduler stopped")
