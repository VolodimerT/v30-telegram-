"""
hermes_analyzer.py — Hermès AI Agent (FastAPI Server)
Анализирует ставки и учится на результатах
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

UTC = timezone.utc
logger = logging.getLogger("hermes_analyzer")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

# ═══════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════

class Match(BaseModel):
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


class AnalyzeRequest(BaseModel):
    matches: List[Match]
    mode: str = "NORMAL"
    strict: bool = False


class LearnRequest(BaseModel):
    match: str
    selection: str
    result: str  # WIN, LOSS, PUSH
    pnl: float
    confidence_was: float


class Recommendation(BaseModel):
    match: str
    hermès_recommendation: str
    confidence: float
    reason: str
    suggested_stake_adjustment: float


class AnalyzeResponse(BaseModel):
    recommendations: List[Recommendation]
    summary: Dict


# ═══════════════════════════════════════════════════════════════════════════
# HERMÈS AI ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class HermèsAnalyzer:
    """AI analyzer for betting picks."""
    
    def __init__(self):
        self.memory_file = "hermes_memory.json"
        self.learning_file = "hermes_learning.json"
        self.memory = self.load_memory()
        self.learning_data = self.load_learning()
        logger.info("✅ Hermès AI Engine initialized")
    
    def load_memory(self) -> Dict:
        """Load Hermès memory."""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, "r") as f:
                    return json.load(f)
            except:
                pass
        return {
            "total_analyzed": 0,
            "total_accepted": 0,
            "total_learned": 0,
            "success_rate": 0.0,
            "avg_confidence": 0.5,
        }
    
    def load_learning(self) -> List[Dict]:
        """Load learning data."""
        if os.path.exists(self.learning_file):
            try:
                with open(self.learning_file, "r") as f:
                    return json.load(f)
            except:
                pass
        return []
    
    def save_memory(self):
        """Save Hermès memory."""
        with open(self.memory_file, "w") as f:
            json.dump(self.memory, f, indent=2)
    
    def save_learning(self):
        """Save learning data."""
        with open(self.learning_file, "w") as f:
            json.dump(self.learning_data, f, indent=2)
    
    async def analyze(
        self,
        matches: List[Match],
        mode: str = "NORMAL",
        strict: bool = False
    ) -> AnalyzeResponse:
        """Analyze matches and return recommendations."""
        
        logger.info(f"Analyzing {len(matches)} matches (mode={mode}, strict={strict})")
        
        recommendations = []
        accepted = 0
        
        for match_obj in matches:
            match = match_obj.match
            ev = match_obj.ev_calibrated
            odds = match_obj.best_odds
            data_quality = match_obj.data_quality
            
            # Simple AI logic
            if ev > 10 and odds > 1.8 and data_quality in ["HIGH", "MEDIUM"]:
                recommendation = "ACCEPT"
                confidence = min(0.95, 0.5 + (ev / 100))
                adjustment = 1.0
                reason = f"Strong EV ({ev:.1f}) with good odds ({odds:.2f})"
                accepted += 1
            
            elif ev > 5 and odds > 1.5:
                recommendation = "RECONSIDER"
                confidence = 0.6
                adjustment = 0.5
                reason = f"Moderate EV ({ev:.1f}), suggest reducing stake"
            
            else:
                recommendation = "REJECT"
                confidence = 0.3
                adjustment = 0.0
                reason = f"Low EV ({ev:.1f}) or poor data quality"
            
            recommendations.append(Recommendation(
                match=match,
                hermès_recommendation=recommendation,
                confidence=confidence,
                reason=reason,
                suggested_stake_adjustment=adjustment
            ))
        
        # Update memory
        self.memory["total_analyzed"] += len(matches)
        self.memory["total_accepted"] += accepted
        self.save_memory()
        
        # Summary
        summary = {
            "hermès_accepts": accepted,
            "hermès_rejects": len(matches) - accepted,
            "average_confidence": sum(r.confidence for r in recommendations) / len(recommendations)
        }
        
        logger.info(f"Analysis complete: {accepted} accepts, {len(matches) - accepted} rejects")
        
        return AnalyzeResponse(
            recommendations=recommendations,
            summary=summary
        )
    
    async def learn(self, request: LearnRequest) -> Dict:
        """Learn from bet results."""
        
        logger.info(f"Learning: {request.match} {request.selection} {request.result} ({request.pnl})")
        
        # Store learning data
        self.learning_data.append({
            "timestamp": datetime.now(UTC).isoformat(),
            "match": request.match,
            "selection": request.selection,
            "result": request.result,
            "pnl": request.pnl,
            "confidence_was": request.confidence_was,
        })
        
        # Update memory
        self.memory["total_learned"] += 1
        
        # Calculate success rate
        wins = sum(1 for d in self.learning_data if d["result"] == "WIN")
        if self.learning_data:
            self.memory["success_rate"] = wins / len(self.learning_data)
        
        self.save_memory()
        self.save_learning()
        
        logger.info(f"Learned: success_rate={self.memory['success_rate']:.1%}")
        
        return {
            "status": "learned",
            "success_rate": self.memory["success_rate"],
            "total_learned": self.memory["total_learned"]
        }
    
    async def get_stats(self) -> Dict:
        """Get Hermès statistics."""
        return {
            "total_analyzed": self.memory["total_analyzed"],
            "total_accepted": self.memory["total_accepted"],
            "total_learned": self.memory["total_learned"],
            "accuracy": self.memory["success_rate"],
            "avg_confidence": self.memory["avg_confidence"],
        }


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════════════════

app = FastAPI(title="Hermès AI Agent", version="2.0")
hermès = HermèsAnalyzer()


@app.on_event("startup")
async def startup():
    logger.info("=" * 80)
    logger.info("🚀 HERMÈS AI AGENT (ETAP 2) STARTING")
    logger.info("=" * 80)


@app.on_event("shutdown")
async def shutdown():
    logger.info("=" * 80)
    logger.info("🛑 HERMÈS AI AGENT SHUTDOWN")
    logger.info("=" * 80)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "hermès_analyzer"}


@app.post("/analyze")
async def analyze(request: AnalyzeRequest):
    """Analyze matches and return recommendations."""
    try:
        response = await hermès.analyze(
            matches=request.matches,
            mode=request.mode,
            strict=request.strict
        )
        return response
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/learn")
async def learn(request: LearnRequest):
    """Learn from bet results."""
    try:
        result = await hermès.learn(request)
        return result
    except Exception as e:
        logger.error(f"Learning error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats")
async def stats():
    """Get Hermès statistics."""
    try:
        return await hermès.get_stats()
    except Exception as e:
        logger.error(f"Stats error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "hermès_analyzer",
        "version": "2.0",
        "status": "running",
        "endpoints": {
            "/health": "Health check",
            "/analyze": "POST - Analyze matches",
            "/learn": "POST - Learn from results",
            "/stats": "GET - Get statistics"
        }
    }


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Starting Hermès on port {port}...")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
