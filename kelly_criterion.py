"""
Kelly Criterion for bet sizing
"""
import math

def calculate_kelly_fraction(win_prob, odds):
    """
    Kelly Criterion: f* = (bp - q) / b
    where:
    - b = odds - 1 (decimal odds converted)
    - p = probability of winning
    - q = probability of losing (1 - p)
    """
    if not (0 < win_prob < 1):
        return 0
    
    if odds < 1.0:
        return 0
    
    b = odds - 1
    p = win_prob
    q = 1 - p
    
    kelly = (b * p - q) / b
    
    # Never bet more than Kelly fraction
    # Use 25% of Kelly for safety (Fractional Kelly)
    fractional_kelly = kelly * 0.25
    
    # Clamp between 0 and 1
    return max(0, min(1, fractional_kelly))

def calculate_bet_size(bank, kelly_fraction, min_bet=10, max_bet=500):
    """
    Calculate optimal bet size based on Kelly fraction
    """
    if kelly_fraction <= 0:
        return min_bet
    
    bet = bank * kelly_fraction
    bet = max(min_bet, min(bet, max_bet))
    
    return bet

def calculate_expected_value(odds, win_prob, bet_size):
    """
    Expected value of a bet
    EV = (odds * win_prob - (1 - win_prob)) * bet_size
    """
    ev = (odds * win_prob - (1 - win_prob)) * bet_size
    return ev
