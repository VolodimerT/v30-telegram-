"""
main_autonomous.py - BETTING BOT v6.1
CRITICAL FIX: btts removed from /sports/{sport}/odds endpoint
BTTS = Additional market → needs /events/{eventId}/odds endpoint
Architecture:
  - Phase 1: /sports/{sport}/odds  → markets = h2h,spreads,totals  (featured)
  - Phase 2: /sports/{sport}/events → get event IDs
            /events/{id}/odds     → markets = btts,draw_no_bet    (additional)
"""
import os, json, logging, asyncio, httpx
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from hermes_integration_etap2 import init_hermes, enrich_picks_with_hermes
from markets_config_simple import EXPANDED_MATCHES
from kelly_criterion import calculate_kelly_fraction, calculate_bet_size
from analytics import analytics

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID_STR  = os.getenv("CHAT_ID", "")
CHAT_ID      = int(CHAT_ID_STR) if CHAT_ID_STR else None
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
UTC          = timezone.utc
scheduler    = AsyncIOScheduler()
INITIAL_BANK = 1019
ALL_MATCHES  = None

# ── PARAMETERS ───────────────────────────────────────────────────────────────
MIN_ODDS  = 1.80
MAX_ODDS  = 5.00
MIN_KELLY = 0.005
MIN_CONF  = 0.50
TOP_N     = 40

# Phase 1 — featured markets (all sports, standard endpoint)
FEATURED_MARKETS = "h2h,spreads,totals"

# Phase 2 — additional markets (per-event, soccer only)
# btts is an "additional" market → /events/{id}/odds only
ADDITIONAL_MARKETS = "btts,draw_no_bet"
SOCCER_SPORTS = [
    "soccer_brazil_serie_a", "soccer_brazil_serie_b",
    "soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga",
    "soccer_italy_serie_a", "soccer_france_ligue_one",
    "soccer_uefa_champs_league", "soccer_uefa_europa_league"
]
# ─────────────────────────────────────────────────────────────────────────────

logger.info(f"BOT v6.1 | CHAT_ID={CHAT_ID} | MIN_ODDS={MIN_ODDS}")


# ── HELPERS ──────────────────────────────────────────────────────────────────

def today_window():
    now       = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end   = day_start + timedelta(hours=47)
    fmt       = "%Y-%m-%dT%H:%M:%SZ"
    return day_start.strftime(fmt), day_end.strftime(fmt)


def market_label(mk_key, name, point):
    if mk_key == "h2h":
        return f"h2h: {name}"
    if mk_key == "spreads":
        pt = f"{point:+.1f}" if point is not None else ""
        return f"hcap{pt}: {name}"
    if mk_key == "totals":
        pt = str(point) if point is not None else ""
        return f"total{pt}: {name}"
    if mk_key == "btts":
        return f"btts: {name}"
    if mk_key == "draw_no_bet":
        return f"dnb: {name}"
    return f"{mk_key}: {name}"


def calc_dynamic_confidence(odds, mk_key, bk_count=1):
    if mk_key == "spreads":
        if 1.85 <= odds <= 2.20:
            base = 0.62
        elif 2.20 < odds <= 3.0:
            base = 0.58
        else:
            base = 0.52
    elif mk_key == "totals":
        if 1.80 <= odds <= 2.10:
            base = 0.58
        elif 2.10 < odds <= 3.0:
            base = 0.55
        else:
            base = 0.50
    elif mk_key == "btts":
        if 1.70 <= odds <= 2.10:
            base = 0.63
        elif 2.10 < odds <= 2.80:
            base = 0.57
        else:
            base = 0.50
    elif mk_key == "draw_no_bet":
        if 1.80 <= odds <= 2.50:
            base = 0.60
        else:
            base = 0.53
    else:  # h2h
        if 2.0 <= odds <= 3.5:
            base = 0.62
        elif 1.80 <= odds < 2.0:
            base = 0.55
        elif 3.5 < odds <= 4.5:
            base = 0.55
        else:
            base = 0.48
    return round(min(0.90, base + min(0.05, (bk_count - 1) * 0.01)), 3)


def calc_ev(odds, conf):
    return round(conf * (odds - 1) - (1 - conf), 4)


def smart_override(pick):
    rec    = pick.get("hermes_recommendation", "REJECT")
    conf   = pick.get("hermes_confidence", 0.3)
    ev     = pick.get("ev_score", 0)
    dyn    = pick.get("dynamic_confidence", 0.5)
    mk     = pick.get("market_type", "h2h")
    if rec != "REJECT":
        return pick
    ev_strong   = 0.15 if mk in ("h2h", "btts", "draw_no_bet") else 0.20
    ev_moderate = 0.05 if mk in ("h2h", "btts", "draw_no_bet") else 0.10
    if ev > ev_strong and dyn >= 0.62:
        pick["hermes_recommendation"] = "ACCEPT"
        pick["hermes_confidence"]     = max(conf, dyn)
        pick["override_reason"]       = f"strong_ev({ev:.2f})"
    elif ev > ev_moderate and dyn >= 0.55:
        pick["hermes_recommendation"] = "RECONSIDER"
        pick["hermes_confidence"]     = max(conf, dyn)
        pick["override_reason"]       = f"positive_ev({ev:.2f})"
    return pick


# ── STORAGE ──────────────────────────────────────────────────────────────────

class Storage:
    def __init__(self):
        self.recs    = self._load("recommendations.json")
        self.bets    = self._load("user_bets.json")
        self.results = self._load("bet_results.json")
        self.app     = None
        self.bank    = INITIAL_BANK

    def _load(self, f):
        try:
            return json.load(open(f))
        except:
            return []

    def save(self):
        try:
            json.dump(self.recs,    open("recommendations.json","w"), indent=2)
            json.dump(self.bets,    open("user_bets.json","w"),       indent=2)
            json.dump(self.results, open("bet_results.json","w"),     indent=2)
        except Exception as e:
            logger.error(f"Save error: {e}")

    def set_app(self, app): self.app = app

    def add_rec(self, match, market, odds, conf, rec):
        self.recs.append({
            "id": len(self.recs)+1, "match": match, "market": market,
            "odds": odds, "confidence": conf, "recommendation": rec,
            "timestamp": datetime.now(UTC).isoformat()
        })
        self.save()
        try:
            analytics.record_pick(match.split()[0], market, rec, conf)
        except:
            pass

    def add_bet(self, match, market, odds, stake):
        kf  = calculate_kelly_fraction(0.55, odds)
        opt = calculate_bet_size(self.bank, kf, min_bet=10, max_bet=int(self.bank*0.1))
        bet = {
            "id": len(self.bets)+1, "match": match, "market": market,
            "odds": odds, "stake": stake, "optimal_stake": opt,
            "kelly_fraction": kf, "timestamp": datetime.now(UTC).isoformat(),
            "status": "OPEN"
        }
        self.bets.append(bet)
        self.save()
        return bet

    def get_stats(self):
        if not self.results:
            return {"total":0,"wins":0,"losses":0,"profit":0,
                    "bank":self.bank,"wr":0,"roi":0}
        w  = sum(1 for r in self.results if r.get("result")=="WON")
        l  = sum(1 for r in self.results if r.get("result")=="LOST")
        p  = sum(r.get("profit",0) for r in self.results)
        ts = sum(r.get("stake",0) for r in self.results)
        return {"total":len(self.results),"wins":w,"losses":l,
                "profit":p,"bank":self.bank,
                "wr": w/len(self.results)*100 if self.results else 0,
                "roi": p/ts*100 if ts>0 else 0}

    async def tg(self, msg):
        if not self.app or not CHAT_ID:
            return
        try:
            await self.app.bot.send_message(
                chat_id=CHAT_ID, text=msg, parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"TG error: {e}")


storage = Storage()


# ── PHASE 2: BTTS via /events/{id}/odds ──────────────────────────────────────

async def fetch_btts_for_sport(client, sport_key, date_from, date_to):
    """
    BTTS is an 'additional market' — needs per-event endpoint.
    Step 1: GET /sports/{sport}/events  → list of event IDs for today
    Step 2: GET /events/{id}/odds?markets=btts,draw_no_bet  per event
    Cost: 1 quota per event (low cost)
    """
    btts_results = []
    try:
        # Step 1 — event list
        r = await client.get(
            f"https://api.the-odds-api.com/v4/sports/{sport_key}/events",
            params={
                "apiKey":           ODDS_API_KEY,
                "commenceTimeFrom": date_from,
                "commenceTimeTo":   date_to,
            },
            timeout=10.0
        )
        if r.status_code != 200:
            logger.debug(f"[BTTS] {sport_key} events HTTP {r.status_code}")
            return btts_results

        events = r.json()
        logger.info(f"[BTTS] {sport_key}: {len(events)} events for BTTS lookup")

        # Step 2 — per-event odds (limit to 10 events to save quota)
        for ev in events[:10]:
            ev_id = ev.get("id")
            home  = ev.get("home_team","Team1")
            away  = ev.get("away_team","Team2")
            if not ev_id:
                continue

            try:
                ro = await client.get(
                    f"https://api.the-odds-api.com/v4/sports/{sport_key}/events/{ev_id}/odds",
                    params={
                        "apiKey":     ODDS_API_KEY,
                        "regions":    "eu,uk",
                        "markets":    ADDITIONAL_MARKETS,
                        "oddsFormat": "decimal"
                    },
                    timeout=8.0
                )
                if ro.status_code == 200:
                    ev_data = ro.json()
                    ev_data["home_team"] = home
                    ev_data["away_team"] = away
                    btts_results.append(ev_data)
                    logger.debug(f"  [BTTS] {home} vs {away}: OK")
                elif ro.status_code == 422:
                    logger.debug(f"  [BTTS] {home} vs {away}: btts not available")
                else:
                    logger.debug(f"  [BTTS] {home} vs {away}: HTTP {ro.status_code}")
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.debug(f"  [BTTS] {home} vs {away}: {e}")

    except Exception as e:
        logger.error(f"[BTTS] {sport_key}: {e}")

    logger.info(f"[BTTS] {sport_key}: got BTTS odds for {len(btts_results)} events")
    return btts_results


# ── PHASE 1: FEATURED MARKETS ─────────────────────────────────────────────────

async def fetch_real_matches():
    try:
        logger.info("="*50)
        logger.info("FETCHING TODAY'S MATCHES — v6.1 dual-phase")
        logger.info(f"Phase 1: featured ({FEATURED_MARKETS})")
        logger.info(f"Phase 2: additional ({ADDITIONAL_MARKETS}) for soccer only")
        logger.info("="*50)

        if not ODDS_API_KEY:
            logger.warning("No ODDS_API_KEY — using mock")
            return EXPANDED_MATCHES

        date_from, date_to = today_window()
        logger.info(f"Window: {date_from} → {date_to}")

        async with httpx.AsyncClient(timeout=20.0) as client:
            # ─ Get sports list ─
            sr = await client.get(
                "https://api.the-odds-api.com/v4/sports",
                params={"apiKey": ODDS_API_KEY}
            )
            if sr.status_code != 200:
                logger.error(f"Sports list HTTP {sr.status_code}")
                return EXPANDED_MATCHES

            sports  = [s for s in sr.json() if s.get("active")]
            matches = {}
            total   = 0
            logger.info(f"Active sports: {len(sports)}")

            # ─ Phase 1: featured markets (h2h, spreads, totals) ─
            for sport in sports:
                sk = sport.get("key","")
                if not sk:
                    continue
                try:
                    r = await client.get(
                        f"https://api.the-odds-api.com/v4/sports/{sk}/odds",
                        params={
                            "apiKey":           ODDS_API_KEY,
                            "regions":          "eu,uk",
                            "markets":          FEATURED_MARKETS,   # h2h,spreads,totals
                            "commenceTimeFrom": date_from,
                            "commenceTimeTo":   date_to,
                            "oddsFormat":       "decimal"
                        },
                        timeout=12.0
                    )
                    if r.status_code == 200:
                        events = r.json()
                        if events:
                            processed = []
                            for ev in events:
                                home = ev.get("home_team","Team1")
                                away = ev.get("away_team","Team2")
                                processed.append({
                                    "match":      f"{home} vs {away}",
                                    "sport":      sk,
                                    "id":         ev.get("id",""),
                                    "home":       home,
                                    "away":       away,
                                    "commence":   ev.get("commence_time",""),
                                    "bookmakers": ev.get("bookmakers",[]),
                                })
                            matches[sk] = processed
                            total += len(processed)
                            mk_keys = set()
                            for ev in events[:2]:
                                for bk in ev.get("bookmakers",[])[:1]:
                                    for mk in bk.get("markets",[]):
                                        mk_keys.add(mk.get("key",""))
                            logger.info(f"  P1 {sk}: {len(processed)} events | mks={mk_keys}")
                    elif r.status_code == 422:
                        pass
                    else:
                        logger.warning(f"  P1 SKIP {sk}: HTTP {r.status_code}")
                except Exception as e:
                    logger.error(f"  P1 ERR {sk}: {e}")

            # ─ Phase 2: BTTS via per-event endpoint for soccer sports ─
            logger.info(f"Phase 2: BTTS for {len(SOCCER_SPORTS)} soccer leagues...")
            for sk in SOCCER_SPORTS:
                if sk not in matches:
                    # Sport may not have today's matches
                    continue
                btts_events = await fetch_btts_for_sport(client, sk, date_from, date_to)
                if not btts_events:
                    continue

                # Merge BTTS bookmakers into existing events by home/away name
                for btts_ev in btts_events:
                    home = btts_ev.get("home_team","")
                    away = btts_ev.get("away_team","")
                    bks  = btts_ev.get("bookmakers",[])
                    # Find matching event in Phase 1 results
                    for ev in matches.get(sk,[]):
                        if ev["home"] == home and ev["away"] == away:
                            ev["bookmakers"] = ev.get("bookmakers",[]) + bks
                            logger.debug(f"  [BTTS merged] {home} vs {away} +{len(bks)} bk")
                            break

            if matches:
                logger.info(f"Total events: {total} | Sports: {len(matches)}")
                return matches
            logger.warning("No matches — using mock")
            return EXPANDED_MATCHES

    except Exception as e:
        logger.error(f"Fetch error: {e}", exc_info=True)
        return EXPANDED_MATCHES


# ── SCAN ─────────────────────────────────────────────────────────────────────

async def scan():
    global ALL_MATCHES
    logger.info("="*50)
    logger.info("SCAN v6.1 (h2h+spreads+totals+btts)")
    logger.info(f"MIN_ODDS={MIN_ODDS} | MAX_ODDS={MAX_ODDS} | MIN_KELLY={MIN_KELLY*100}%")
    logger.info("="*50)

    if not ALL_MATCHES:
        logger.warning("No matches — skip scan")
        return

    raw_picks     = []
    total_raw     = 0
    rejected_odds = 0
    mk_raw_counts = {}

    for sport_key, events in ALL_MATCHES.items():
        for event in events:
            home = event.get("home","")
            away = event.get("away","")
            if not home or not away:
                continue
            match_name = f"{home} vs {away}"
            bookmakers = event.get("bookmakers",[])
            bk_count   = len(bookmakers)
            if not bookmakers:
                continue

            for bk in bookmakers[:2]:
                for market in bk.get("markets",[]):
                    mk_key = market.get("key","")
                    if mk_key not in ("h2h","spreads","totals","btts","draw_no_bet"):
                        continue
                    for outcome in market.get("outcomes",[]):
                        name  = outcome.get("name","")
                        odds  = outcome.get("price",0)
                        point = outcome.get("point",None)
                        total_raw += 1
                        mk_raw_counts[mk_key] = mk_raw_counts.get(mk_key,0)+1
                        if odds < MIN_ODDS or odds > MAX_ODDS:
                            rejected_odds += 1
                            continue
                        dyn_conf = calc_dynamic_confidence(odds, mk_key, bk_count)
                        ev_score = calc_ev(odds, dyn_conf)
                        label    = market_label(mk_key, name, point)
                        raw_picks.append({
                            "match":               match_name,
                            "sport":               sport_key,
                            "league":              sport_key,
                            "market_type":         mk_key,
                            "selection":           label,
                            "odds":                odds,
                            "point":               point,
                            "implied_probability": round(1/odds,4),
                            "bookmaker_count":     bk_count,
                            "dynamic_confidence":  dyn_conf,
                            "confidence":          dyn_conf,
                            "ev_score":            ev_score,
                        })

    logger.info(f"Raw outcomes: {total_raw} | By market: {mk_raw_counts}")
    logger.info(f"Rejected (odds): {rejected_odds} | After filter: {len(raw_picks)}")

    if not raw_picks:
        msg = (f"⚠️ 0 picks in range {MIN_ODDS}-{MAX_ODDS}\n"
               f"Raw: {total_raw} | Markets: {mk_raw_counts}")
        await storage.tg(msg)
        return

    picks = sorted(raw_picks, key=lambda x: x["ev_score"], reverse=True)[:TOP_N]
    mk_counts = {}
    for p in picks:
        mk_counts[p["market_type"]] = mk_counts.get(p["market_type"],0)+1
    logger.info(f"Top {len(picks)} picks | Markets dist: {mk_counts}")

    try:
        enriched       = await enrich_picks_with_hermes(picks, mode="NORMAL")
        enriched_picks = enriched.get("enriched_picks",[])
        sent = filtered_rec = filtered_kelly = 0

        for idx, pick in enumerate(enriched_picks):
            pick.setdefault("ev_score", 0)
            pick.setdefault("dynamic_confidence", 0.5)
            pick.setdefault("market_type", "h2h")
            pick     = smart_override(pick)
            conf     = pick.get("hermes_confidence", 0.3)
            rec      = pick.get("hermes_recommendation", "REJECT")
            odds     = pick.get("odds", 2.0)
            mk_type  = pick.get("market_type","h2h")
            ev       = pick.get("ev_score", calc_ev(odds,conf))
            override = pick.get("override_reason","")

            logger.info(
                f"[{idx+1:02d}] [{mk_type:9s}] {pick['match'][:25]:25s} | "
                f"odds={odds:.2f} ev={ev:+.3f} conf={conf:.0%} rec={rec}"
                + (f" [{override}]" if override else "")
            )

            if rec not in ("ACCEPT","RECONSIDER"):
                filtered_rec += 1
                continue
            if conf < MIN_CONF:
                filtered_rec += 1
                continue

            kf = calculate_kelly_fraction(conf, odds)
            if kf < MIN_KELLY:
                filtered_kelly += 1
                continue

            opt_stake = calculate_bet_size(storage.bank, kf)
            emoji     = "✅" if rec == "ACCEPT" else "⚠️"
            mk_emoji  = {"h2h":"🏆","spreads":"➕","totals":"📊",
                         "btts":"⚽","draw_no_bet":"🛡"}.get(mk_type,"🎯")
            ov_line   = ("\n_override: "+override+"_") if override else ""

            storage.add_rec(pick["match"], pick["selection"], odds, conf, rec)
            msg = (
                emoji + " " + mk_emoji + " *" + pick["sport"].upper() + "*\n\n"
                "*" + pick["match"] + "*\n"
                "`" + pick["selection"] + "`\n"
                f"Odds: `{odds}` | EV: `{ev:+.3f}`\n"
                f"{rec} ({conf:.0%})" + ov_line + "\n\n"
                f"Kelly: {kf*100:.1f}% | Stake: {opt_stake:.0f} UAH\n\n"
                f"/place_bet \"{pick['match']}\" \"{pick['selection']}\" {odds} {opt_stake:.0f}"
            )
            await storage.tg(msg)
            await asyncio.sleep(0.15)
            sent += 1

        logger.info(f"Scan done: sent={sent} | filtered={filtered_rec+filtered_kelly}")
        if sent == 0:
            await storage.tg(
                f"Scan: 0 value picks\n"
                f"Raw: {total_raw} | Markets: {mk_raw_counts}\n"
                f"After filter: {len(raw_picks)} | Filtered by EV/Kelly: {filtered_rec+filtered_kelly}"
            )

    except Exception as e:
        logger.error(f"Scan error: {e}", exc_info=True)


# ── COMMANDS ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "BETTING BOT v6.1\n\n"
        "Markets:\n"
        "- Phase 1: h2h + spreads + totals (all sports)\n"
        "- Phase 2: btts + draw_no_bet (soccer, per-event)\n\n"
        f"Odds: {MIN_ODDS}–{MAX_ODDS} | Kelly: >{MIN_KELLY*100:.0f}%\n\n"
        "/scan /place_bet /bets /bank /stats /analytics /debug /help"
    )


async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Scan started...")
    await scan()


async def debug_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ALL_MATCHES:
        await update.message.reply_text("No matches. Run /scan first.")
        return
    lines = ["DEBUG v6.1\n"]
    total_ev, mk_all = 0, {}
    for sk, events in ALL_MATCHES.items():
        ev_mks = {}
        for ev in events:
            for bk in ev.get("bookmakers",[])[:1]:
                for mk in bk.get("markets",[]):
                    k = mk.get("key","")
                    ev_mks[k] = ev_mks.get(k,0) + len(mk.get("outcomes",[]))
                    mk_all[k] = mk_all.get(k,0) + len(mk.get("outcomes",[]))
        total_ev += len(events)
        if events:
            lines.append(f"{sk}: {len(events)} | {ev_mks}")
    lines += [f"\nTotal: {total_ev}", f"All markets: {mk_all}",
              f"\nMIN_ODDS={MIN_ODDS} | Phase1={FEATURED_MARKETS}",
              f"Phase2={ADDITIONAL_MARKETS} (soccer only)"]
    await update.message.reply_text("\n".join(lines[:40]))


async def place_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 4:
            await update.message.reply_text('/place_bet "Match" "Market" odds stake')
            return
        match  = context.args[0]
        market = context.args[1]
        odds   = float(context.args[2])
        stake  = float(context.args[3])
        if odds < 1.0 or stake <= 0 or stake > storage.bank:
            await update.message.reply_text("Invalid odds/stake or insufficient bank")
            return
        bet = storage.add_bet(match, market, odds, stake)
        await update.message.reply_text(
            f"Bet placed\n{match} | {market}\n"
            f"Odds: {odds} | Stake: {stake}\n"
            f"Kelly: {bet['kelly_fraction']*100:.1f}% | Optimal: {bet['optimal_stake']:.0f} UAH"
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def bets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    open_bets = [b for b in storage.bets if b.get("status")=="OPEN"]
    if not open_bets:
        await update.message.reply_text("No open bets")
        return
    msg = "OPEN BETS\n\n"
    for b in open_bets:
        msg += f"ID:{b['id']} | {b['match']}\n{b['odds']} @ {b['stake']} UAH\n\n"
    await update.message.reply_text(msg)


async def bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = storage.get_stats()
    await update.message.reply_text(
        f"BANK: {s['bank']:.2f} UAH\n"
        f"Profit: {s['profit']:+.2f} | Bets: {s['total']}\n"
        f"WR: {s['wr']:.1f}% | ROI: {s['roi']:.2f}%"
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = storage.get_stats()
    await update.message.reply_text(
        f"Total: {s['total']} | Wins: {s['wins']} | Losses: {s['losses']}\n"
        f"WR: {s['wr']:.1f}% | Profit: {s['profit']:+.2f} | ROI: {s['roi']:.2f}%"
    )


async def analytics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        report = analytics.get_report()
    except:
        report = "Analytics not available"
    await update.message.reply_text(report)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — info\n"
        "/scan — manual scan\n"
        "/debug — API market stats\n"
        "/place_bet — place bet\n"
        "/bets — open bets\n"
        "/bank — bankroll\n"
        "/stats — performance\n"
        "/analytics — analytics\n"
        "/help — help"
    )


# ── SCHEDULER ────────────────────────────────────────────────────────────────

async def refresh_matches():
    global ALL_MATCHES
    logger.info("Daily refresh...")
    ALL_MATCHES = await fetch_real_matches()
    await scan()


async def post_init(app):
    global ALL_MATCHES
    storage.set_app(app)
    logger.info("Bot v6.1 starting...")
    ALL_MATCHES = await fetch_real_matches()
    try:
        await init_hermes()
        logger.info("Hermes OK")
    except Exception as e:
        logger.error(f"Hermes: {e}")
    if not scheduler.running:
        scheduler.start()
        scheduler.add_job(scan,            "cron", hour="*/1", minute=0, id="scan_hourly")
        scheduler.add_job(refresh_matches, "cron", hour=6,     minute=0, id="refresh_daily")
        logger.info("Scheduler OK")
    await scan()
    try:
        await storage.tg(
            "Bot v6.1 started\n"
            "Phase1: h2h+spreads+totals | Phase2: btts+dnb (soccer)\n"
            f"Odds: {MIN_ODDS}–{MAX_ODDS}"
        )
    except:
        pass


async def post_stop(app):
    if scheduler.running:
        scheduler.shutdown()


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TOKEN).build()
    app.post_init = post_init
    app.post_stop = post_stop
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("scan",      scan_cmd))
    app.add_handler(CommandHandler("debug",     debug_cmd))
    app.add_handler(CommandHandler("place_bet", place_bet, filters.TEXT))
    app.add_handler(CommandHandler("bets",      bets))
    app.add_handler(CommandHandler("bank",      bank))
    app.add_handler(CommandHandler("stats",     stats_cmd))
    app.add_handler(CommandHandler("analytics", analytics_cmd))
    app.add_handler(CommandHandler("help",      help_cmd))
    logger.info("Polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
