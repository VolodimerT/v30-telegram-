"""hermes_analyzer.py — Hermès Agent API for match analysis (Phase 8 - Etap 1)."""
import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

UTC = timezone.utc
BASE_DIR = Path(__file__).resolve().parent
MEMORY_DIR = BASE_DIR / "hermes_memory"
MEMORY_DIR.mkdir(exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════

class Match(BaseModel):
    """Match data from betting bot."""
    match: str
    sport: str
    market: str
    selection: str
    best_odds: float
    book_count: int
    ev_calibrated: float
    ci_low: float
    data_quality: str
    decision: str
    stake: float


class MatchAnalysisRequest(BaseModel):
    """Request to analyze multiple matches."""
    matches: List[Match]
    mode: str = "NORMAL"
    strict: bool = False


class HermèsRecommendation(BaseModel):
    """Hermès recommendation for a match."""
    match: str
    selection: str
    original_decision: str
    hermès_recommendation: str  # "ACCEPT", "RECONSIDER", "REJECT"
    confidence: float  # 0.0 to 1.0
    reason: str
    suggested_stake_adjustment: float  # 1.0 = no change, 0.5 = half, 2.0 = double


class MatchAnalysisResponse(BaseModel):
    """Response with Hermès analysis."""
    timestamp: str
    total_matches: int
    recommendations: List[HermèsRecommendation]
    summary: Dict
    memory_update: str


class LearningResult(BaseModel):
    """Tell Hermès about actual result."""
    match: str
    selection: str
    result: str  # "WIN", "LOSS", "PUSH"
    pnl: float
    confidence_was: float


# ═══════════════════════════════════════════════════════════════════════════
# HERMÈS MEMORY MANAGER
# ═══════════════════════════════════════════════════════════════════════════

class HermèsMemory:
    """Hermès long-term memory system."""
    
    def __init__(self):
        self.memory_file = MEMORY_DIR / "hermes_memory.json"
        self.learning_file = MEMORY_DIR / "hermes_learning.json"
        self.memory = self._load_memory()
        self.learning_history = self._load_learning()
    
    def _load_memory(self) -> Dict:
        """Load memory from disk."""
        if self.memory_file.exists():
            try:
                return json.loads(self.memory_file.read_text())
            except Exception:
                pass
        return {
            "created_at": datetime.now(UTC).isoformat(),
            "analysis_count": 0,
            "decisions_made": 0,
            "accuracy_rate": 0.0,
            "favorite_sports": {},
            "patterns_learned": [],
            "confidence_by_sport": {},
        }
    
    def _load_learning(self) -> List[Dict]:
        """Load learning history."""
        if self.learning_file.exists():
            try:
                return json.loads(self.learning_file.read_text())
            except Exception:
                pass
        return []
    
    def save_memory(self) -> None:
        """Save memory to disk."""
        try:
            self.memory_file.write_text(json.dumps(self.memory, indent=2))
        except Exception as e:
            print(f"Failed to save memory: {e}")
    
    def save_learning(self) -> None:
        """Save learning history."""
        try:
            self.learning_file.write_text(json.dumps(self.learning_history, indent=2))
        except Exception as e:
            print(f"Failed to save learning: {e}")
    
    def record_analysis(self, sport: str, decision: str) -> None:
        """Record that Hermès made an analysis."""
        self.memory["analysis_count"] += 1
        self.memory["decisions_made"] += 1
        
        if sport not in self.memory["favorite_sports"]:
            self.memory["favorite_sports"][sport] = 0
        self.memory["favorite_sports"][sport] += 1
        
        if sport not in self.memory["confidence_by_sport"]:
            self.memory["confidence_by_sport"][sport] = {"correct": 0, "total": 0}
        
        self.save_memory()
    
    def learn_from_result(self, match: str, result: str, confidence: float) -> None:
        """Hermès learns from betting result."""
        entry = {
            "match": match,
            "result": result,
            "confidence_was": confidence,
            "learned_at": datetime.now(UTC).isoformat(),
        }
        self.learning_history.append(entry)
        
        # Update accuracy
        if len(self.learning_history) > 0:
            correct = sum(1 for x in self.learning_history if x["result"] == "WIN")
            self.memory["accuracy_rate"] = correct / len(self.learning_history)
        
        self.save_learning()
        self.save_memory()
    
    def get_stats(self) -> Dict:
        """Get Hermès statistics."""
        return {
            "analyses_performed": self.memory["analysis_count"],
            "decisions_made": self.memory["decisions_made"],
            "accuracy_rate": round(self.memory["accuracy_rate"], 3),
            "favorite_sports": self.memory["favorite_sports"],
            "total_learning_entries": len(self.learning_history),
        }


# ═══════════════════════════════════════════════════════════════════════════
# HERMÈS ANALYZER
# ═══════════════════════════════════════════════════════════════════════════

class HermèsAnalyzer:
    """Hermès Agent - Advanced match analyzer."""
    
    def __init__(self):
        self.memory = HermèsMemory()
        self.version = "1.0.0-beta"
    
    def analyze_match(self, match: Match) -> HermèsRecommendation:
        """Analyze single match with Hermès logic."""
        
        recommendation = "ACCEPT"
        confidence = 0.0
        reason = ""
        stake_adjustment = 1.0
        
        # ═══════════════════════════════════════════════════════════════════
        # HERMÈS ANALYSIS LOGIC (ETAP 1 - BASIC)
        # ═══════════════════════════════════════════════════════════════════
        
        # Rule 1: EV and CI Check
        if match.ev_calibrated < 3.0:
            recommendation = "REJECT"
            confidence = 0.8
            reason = f"EV too low ({match.ev_calibrated}%)"
            return HermèsRecommendation(
                match=match.match,
                selection=match.selection,
                original_decision=match.decision,
                hermès_recommendation=recommendation,
                confidence=confidence,
                reason=reason,
                suggested_stake_adjustment=stake_adjustment,
            )
        
        # Rule 2: Data Quality Check
        dq_scores = {"HIGH": 1.0, "MEDIUM": 0.7, "LOW": 0.4}
        dq_score = dq_scores.get(match.data_quality, 0.5)
        
        if dq_score < 0.5:
            recommendation = "RECONSIDER"
            confidence = 0.5
            reason = f"Data quality is {match.data_quality}"
            stake_adjustment = 0.5
        
        # Rule 3: Book Count Check
        if match.book_count >= 8:
            confidence += 0.15
            reason += " | Strong consensus (8+ books)"
        elif match.book_count < 3:
            recommendation = "REJECT"
            confidence = 0.7
            reason = f"Too few bookmakers ({match.book_count})"
        
        # Rule 4: CI Check
        if match.ci_low < 0:
            recommendation = "REJECT"
            confidence = 0.9
            reason = "CI low is negative - high variance"
        elif match.ci_low > 2.0:
            confidence += 0.2
            reason += " | High confidence interval"
        
        # Rule 5: Market-specific rules
        if match.market == "h2h":
            confidence += 0.1
            reason += " | H2H market (reliable)"
        elif match.market in ("spreads", "totals"):
            if match.data_quality != "HIGH":
                stake_adjustment = 0.7
                reason += " | Spread/Total requires high data quality"
        
        # Rule 6: Sport-specific adjustments
        sport_multipliers = {
            "football": 1.0,
            "basketball": 1.1,
            "tennis": 0.8,
            "hockey": 0.9,
        }
        confidence *= sport_multipliers.get(match.sport, 1.0)
        
        # Rule 7: Decision alignment
        if match.decision == "CORE":
            confidence += 0.25
            reason += " | Already classified as CORE"
        elif match.decision == "SUPPORT":
            confidence += 0.15
            reason += " | Already classified as SUPPORT"
        elif match.decision == "MICRO":
            confidence += 0.05
            reason += " | Already classified as MICRO"
        elif match.decision == "PASS":
            recommendation = "RECONSIDER"
            confidence = 0.3
            reason = "Bot already flagged as PASS"
        
        # Cap confidence at 1.0
        confidence = min(confidence, 1.0)
        
        # Final recommendation based on confidence
        if recommendation == "ACCEPT":
            if confidence < 0.4:
                recommendation = "RECONSIDER"
        
        return HermèsRecommendation(
            match=match.match,
            selection=match.selection,
            original_decision=match.decision,
            hermès_recommendation=recommendation,
            confidence=round(confidence, 3),
            reason=reason if reason else "Standard match profile",
            suggested_stake_adjustment=round(stake_adjustment, 2),
        )
    
    def analyze_batch(self, request: MatchAnalysisRequest) -> MatchAnalysisResponse:
        """Analyze batch of matches."""
        
        recommendations = []
        accepted_count = 0
        rejected_count = 0
        
        for match in request.matches:
            rec = self.analyze_match(match)
            recommendations.append(rec)
            
            if rec.hermès_recommendation == "ACCEPT":
                accepted_count += 1
            elif rec.hermès_recommendation == "REJECT":
                rejected_count += 1
            
            self.memory.record_analysis(match.sport, rec.hermès_recommendation)
        
        # Summary
        summary = {
            "total_analyzed": len(request.matches),
            "hermès_accepts": accepted_count,
            "hermès_rejects": rejected_count,
            "hermès_reconsiders": len(request.matches) - accepted_count - rejected_count,
            "average_confidence": round(
                sum(r.confidence for r in recommendations) / len(recommendations) if recommendations else 0,
                3
            ),
        }
        
        return MatchAnalysisResponse(
            timestamp=datetime.now(UTC).isoformat(),
            total_matches=len(request.matches),
            recommendations=recommendations,
            summary=summary,
            memory_update=f"Memory updated. Total analyses: {self.memory.memory['analysis_count']}",
        )


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="Hermès Agent API",
    description="Autonomous AI agent for betting analysis",
    version="1.0.0-beta",
)

hermes = HermèsAnalyzer()


@app.get("/health")
async def health():
    """Health check."""
    return {
        "status": "alive",
        "agent": "Hermès",
        "version": hermes.version,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.post("/analyze")
async def analyze(request: MatchAnalysisRequest) -> MatchAnalysisResponse:
    """Analyze matches with Hermès AI."""
    
    if not request.matches:
        raise HTTPException(status_code=400, detail="No matches provided")
    
    response = hermes.analyze_batch(request)
    return response


@app.post("/learn")
async def learn(result: LearningResult):
    """Tell Hermès about actual betting result."""
    
    hermes.memory.learn_from_result(
        match=result.match,
        result=result.result,
        confidence=result.confidence_was,
    )
    
    return {
        "status": "learned",
        "match": result.match,
        "result": result.result,
        "message": f"Hermès learned from {result.match}: {result.result}",
    }


@app.get("/stats")
async def stats():
    """Get Hermès statistics."""
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "hermès_stats": hermes.memory.get_stats(),
    }


@app.get("/memory")
async def get_memory():
    """Get Hermès memory."""
    return {
        "memory": hermes.memory.memory,
        "learning_entries": len(hermes.memory.learning_history),
    }


@app.post("/reset")
async def reset_memory():
    """Reset Hermès memory (for testing)."""
    hermes.memory.memory = {
        "created_at": datetime.now(UTC).isoformat(),
        "analysis_count": 0,
        "decisions_made": 0,
        "accuracy_rate": 0.0,
        "favorite_sports": {},
        "patterns_learned": [],
        "confidence_by_sport": {},
    }
    hermes.memory.learning_history = []
    hermes.memory.save_memory()
    hermes.memory.save_learning()
    
    return {"status": "memory_reset"}


# ═══════════════════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.getenv("HERMES_PORT", 8000))
    print(f"🧠 Starting Hermès Agent on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
