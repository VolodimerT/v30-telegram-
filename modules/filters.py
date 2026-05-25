import logging

logger = logging.getLogger('scanner')

class SmartFilter:
    def __init__(self, config: dict):
        self.config = config
    
    async def should_place_bet(self, event, market, selection, odds, probability, **kwargs):
        return False, "Filter disabled", {}
