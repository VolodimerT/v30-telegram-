"""
Analytics and statistics tracking
"""
import json
from datetime import datetime, timezone

class Analytics:
    def __init__(self):
        self.stats_file = "analytics_stats.json"
        self.stats = self.load_stats()
    
    def load_stats(self):
        try:
            return json.load(open(self.stats_file))
        except:
            return {
                "total_picks": 0,
                "total_bets": 0,
                "wins": 0,
                "losses": 0,
                "total_stake": 0,
                "total_profit": 0,
                "by_market": {},
                "by_sport": {},
                "by_recommendation": {}
            }
    
    def save_stats(self):
        json.dump(self.stats, open(self.stats_file, "w"), indent=2)
    
    def record_pick(self, sport, market, recommendation, confidence):
        self.stats["total_picks"] += 1
        
        # By market
        if market not in self.stats["by_market"]:
            self.stats["by_market"][market] = {"picks": 0, "wins": 0, "roi": 0}
        self.stats["by_market"][market]["picks"] += 1
        
        # By sport
        if sport not in self.stats["by_sport"]:
            self.stats["by_sport"][sport] = {"picks": 0, "wins": 0, "roi": 0}
        self.stats["by_sport"][sport]["picks"] += 1
        
        # By recommendation
        if recommendation not in self.stats["by_recommendation"]:
            self.stats["by_recommendation"][recommendation] = {"picks": 0, "wins": 0}
        self.stats["by_recommendation"][recommendation]["picks"] += 1
        
        self.save_stats()
    
    def record_result(self, sport, market, recommendation, stake, profit):
        self.stats["total_bets"] += 1
        self.stats["total_stake"] += stake
        self.stats["total_profit"] += profit
        
        if profit > 0:
            self.stats["wins"] += 1
            self.stats["by_market"][market]["wins"] += 1
            self.stats["by_sport"][sport]["wins"] += 1
            self.stats["by_recommendation"][recommendation]["wins"] += 1
        else:
            self.stats["losses"] += 1
        
        # Calculate ROI
        if self.stats["total_stake"] > 0:
            roi = (self.stats["total_profit"] / self.stats["total_stake"]) * 100
            self.stats["by_market"][market]["roi"] = roi
            self.stats["by_sport"][sport]["roi"] = roi
        
        self.save_stats()
    
    def get_report(self):
        total_bets = self.stats["total_bets"]
        if total_bets == 0:
            return "No bets yet"
        
        wr = (self.stats["wins"] / total_bets) * 100
        roi = (self.stats["total_profit"] / self.stats["total_stake"]) * 100 if self.stats["total_stake"] > 0 else 0
        
        report = f"""
📊 ANALYTICS REPORT

Total Bets: {total_bets}
Wins: {self.stats['wins']} ({wr:.1f}%)
Losses: {self.stats['losses']}
ROI: {roi:.2f}%
Profit: {self.stats['total_profit']:+.2f}

TOP MARKETS:
"""
        
        markets_sorted = sorted(
            self.stats["by_market"].items(),
            key=lambda x: x[1]["roi"],
            reverse=True
        )
        
        for market, data in markets_sorted[:5]:
            report += f"\n{market}: {data['picks']} picks, {data['roi']:.2f}% ROI"
        
        return report

analytics = Analytics()
