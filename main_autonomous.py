"""
main_autonomous.py - BETTING BOT v7.2
STANDALONE — zero external imports except python-telegram-bot + httpx + apscheduler

MARKET COVERAGE (verified against the-odds-api docs):
  Soccer:     h2h + spreads + totals (featured) + btts + draw_no_bet (per-event)
  Basketball: h2h + spreads + totals + h2h_h1 + totals_h1
  Ice Hockey: h2h + spreads + totals + h2h_p1 + h2h_p2 + h2h_p3
  Tennis:     h2h only  (spreads/totals rarely available)
  MMA/Boxing: h2h only
"""

import os, json, logging, asyncio, math
from datetime import datetime, timezone, timedelta

import httpx
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ─── CONFIG ─────────────────────────────────────────────────────────────────
TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID_STR  = os.getenv("CHAT_ID", "")
CHAT_ID      = int(CHAT_ID_STR) if CHAT_ID_STR else None
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")

MIN_ODDS     = 1.60
MAX_ODDS     = 6.00
MIN_EV       = 0.02
MIN_KELLY    = 0.003
TOP_N        = 50
INITIAL_BANK = 1019

UTC          = timezone.utc
scheduler    = AsyncIOScheduler()
ALL_MATCHES  = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


# ─── KELLY (standalone, no external module) ──────────────────────────────────

def kelly_fraction(conf: float, odds: float) -> float:
    """Quarter-Kelly. Returns fraction of bank to bet."""
    b = odds - 1.0
    if b <= 0:
        return 0.0
    raw = (b * conf - (1.0 - conf)) / b
    return max(0.0, round(raw * 0.25, 5))


def bet_size(bank: float, kf: float,
             min_bet: float = 10.0,
             max_fraction: float = 0.08) -> float:
    """Clamp stake between min_bet and max_fraction * bank."""
    if kf <= 0:
        return min_bet
    stake = bank * kf
    stake = max(min_bet, min(stake, bank * max_fraction))
    return round(stake, 2)


# ─── EV + CONFIDENCE ─────────────────────────────────────────────────────────

# (lo, hi, model_conf)  — calibrated per market type
CONF_TABLE = {
    "h2h": [
        (1.20, 1.50, 0.78), (1.50, 1.80, 0.68),
        (1.80, 2.20, 0.60), (2.20, 3.00, 0.57),
        (3.00, 4.00, 0.54), (4.00, 6.00, 0.50),
    ],
    "spreads": [
        (1.60, 1.80, 0.64), (1.80, 2.00, 0.62),
        (2.00, 2.30, 0.59), (2.30, 3.00, 0.56),
        (3.00, 6.00, 0.51),
    ],
    "totals": [
        (1.60, 1.80, 0.61), (1.80, 2.00, 0.58),
        (2.00, 2.30, 0.56), (2.30, 3.00, 0.54),
        (3.00, 6.00, 0.50),
    ],
    "btts": [
        (1.50, 1.80, 0.67), (1.80, 2.20, 0.63),
        (2.20, 3.00, 0.57), (3.00, 6.00, 0.50),
    ],
    "draw_no_bet": [
        (1.50, 1.90, 0.65), (1.90, 2.50, 0.61),
        (2.50, 4.00, 0.55), (4.00, 6.00, 0.50),
    ],
    "h2h_h1": [
        (1.60, 2.00, 0.63), (2.00, 2.50, 0.59),
        (2.50, 4.00, 0.54), (4.00, 6.00, 0.50),
    ],
    "totals_h1": [
        (1.60, 2.00, 0.58), (2.00, 2.30, 0.56),
        (2.30, 3.00, 0.53), (3.00, 6.00, 0.50),
    ],
    "spreads_h1": [
        (1.60, 2.00, 0.60), (2.00, 2.30, 0.57),
        (2.30, 3.00, 0.54), (3.00, 6.00, 0.50),
    ],
    "h2h_p1": [
        (1.60, 2.10, 0.61), (2.10, 2.80, 0.57),
        (2.80, 4.00, 0.53), (4.00, 6.00, 0.50),
    ],
    "h2h_p2": [
        (1.60, 2.10, 0.60), (2.10, 2.80, 0.56),
        (2.80, 4.00, 0.52), (4.00, 6.00, 0.50),
    ],
    "h2h_p3": [
        (1.60, 2.10, 0.59), (2.10, 2.80, 0.55),
        (2.80, 4.00, 0.51), (4.00, 6.00, 0.50),
    ],
}


def get_conf(mk_key: str, odds: float) -> float:
    bands = CONF_TABLE.get(mk_key, CONF_TABLE["h2h"])
    for lo, hi, base in bands:
        if lo <= odds < hi:
            return base
    return 0.50


def calc_ev(odds: float, conf: float) -> float:
    return round(conf * (odds - 1.0) - (1.0 - conf), 4)


# ─── SPORT → MARKET MAPPING ──────────────────────────────────────────────────

SPORT_MARKETS = {
    "soccer": {
        "featured":   "h2h,spreads,totals",
        "additional": "btts,draw_no_bet",
        "accept":     {"h2h","spreads","totals","btts","draw_no_bet"},
    },
    "basketball": {
        "featured":   "h2h,spreads,totals,h2h_h1,totals_h1",
        "additional": None,
        "accept":     {"h2h","spreads","totals","h2h_h1","totals_h1"},
    },
    "icehockey": {
        "featured":   "h2h,spreads,totals,h2h_p1,h2h_p2,h2h_p3",
        "additional": None,
        "accept":     {"h2h","spreads","totals","h2h_p1","h2h_p2","h2h_p3"},
    },
    "tennis":  {"featured": "h2h", "additional": None, "accept": {"h2h"}},
    "mma":     {"featured": "h2h", "additional": None, "accept": {"h2h"}},
    "boxing":  {"featured": "h2h", "additional": None, "accept": {"h2h"}},
    "baseball":{"featured": "h2h,spreads,totals", "additional": None,
                "accept": {"h2h","spreads","totals"}},
    "default": {"featured": "h2h,spreads,totals", "additional": None,
                "accept": {"h2h","spreads","totals"}},
}

MK_EMOJI = {
    "h2h":"🏆","spreads":"➕","totals":"📊",
    "btts":"⚽","draw_no_bet":"🛡",
    "h2h_h1":"½🏆","totals_h1":"½📊","spreads_h1":"½➕",
    "h2h_p1":"P1🏒","h2h_p2":"P2🏒","h2h_p3":"P3🏒",
}

# Small EV bonus so non-h2h markets surface first in sorted list
MK_BONUS = {
    "spreads":0.05,"totals":0.05,"btts":0.07,"draw_no_bet":0.04,
    "h2h_h1":0.03,"totals_h1":0.03,"h2h_p1":0.04,"h2h_p2":0.04,"h2h_p3":0.04,
}


def sport_cat(sk: str) -> str:
    if "soccer"     in sk: return "soccer"
    if "basketball" in sk: return "basketball"
    if "icehockey"  in sk: return "icehockey"
    if "tennis"     in sk: return "tennis"
    if "mma"        in sk: return "mma"
    if "boxing"     in sk: return "boxing"
    if "baseball"   in sk: return "baseball"
    return "default"


def market_label(mk_key: str, name: str, point) -> str:
    p = f"{point:+.1f}" if isinstance(point,(int,float)) and point is not None else ""
    labels = {
        "h2h":         f"h2h: {name}",
        "spreads":     f"hcap{p}: {name}",
        "totals":      f"total {p}: {name}",
        "btts":        f"btts: {name}",
        "draw_no_bet": f"dnb: {name}",
        "h2h_h1":      f"1H h2h: {name}",
        "totals_h1":   f"1H total {p}: {name}",
        "spreads_h1":  f"1H hcap{p}: {name}",
        "h2h_p1":      f"P1 h2h: {name}",
        "h2h_p2":      f"P2 h2h: {name}",
        "h2h_p3":      f"P3 h2h: {name}",
    }
    return labels.get(mk_key, f"{mk_key}: {name}")


# ─── STORAGE ─────────────────────────────────────────────────────────────────

class Storage:
    def __init__(self):
        self.bets    = self._load("user_bets.json")
        self.results = self._load("bet_results.json")
        self.bank    = INITIAL_BANK
        self.app     = None

    @staticmethod
    def _load(path):
        try:
            return json.load(open(path))
        except Exception:
            return []

    def save(self):
        try:
            json.dump(self.bets,    open("user_bets.json","w"),  indent=2)
            json.dump(self.results, open("bet_results.json","w"),indent=2)
        except Exception as e:
            logger.error(f"Storage.save: {e}")

    def set_app(self, app): self.app = app

    def add_bet(self, match, market, odds, stake):
        mk_g = "spreads" if "hcap" in market else (
               "totals"  if "total" in market else "h2h")
        kf  = kelly_fraction(get_conf(mk_g, odds), odds)
        opt = bet_size(self.bank, kf)
        bet = {
            "id": len(self.bets)+1, "match": match, "market": market,
            "odds": odds, "stake": stake, "optimal_stake": opt,
            "kelly": kf, "timestamp": datetime.now(UTC).isoformat(),
            "status": "OPEN",
        }
        self.bets.append(bet)
        self.save()
        return bet

    def stats(self):
        total = len(self.results)
        if not total:
            return dict(total=0,wins=0,losses=0,profit=0,
                        bank=self.bank,wr=0.0,roi=0.0)
        w  = sum(1 for r in self.results if r.get("result")=="WON")
        l  = sum(1 for r in self.results if r.get("result")=="LOST")
        pr = sum(r.get("profit",0) for r in self.results)
        ts = sum(r.get("stake",0)  for r in self.results)
        return dict(total=total,wins=w,losses=l,profit=pr,
                    bank=self.bank,wr=w/total*100,
                    roi=pr/ts*100 if ts else 0.0)

    async def tg(self, text: str):
        if not (self.app and CHAT_ID):
            return
        try:
            await self.app.bot.send_message(
                chat_id=CHAT_ID, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"TG send: {e}")


storage = Storage()


# ─── DATE WINDOW ─────────────────────────────────────────────────────────────

def today_window():
    now   = datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end   = start + timedelta(hours=47)
    fmt   = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), end.strftime(fmt)


# ─── PHASE 2: BTTS PER-EVENT (soccer only) ───────────────────────────────────

async def fetch_btts(client: httpx.AsyncClient,
                     sk: str, date_from: str, date_to: str) -> dict:
    """Returns {(home,away): [bookmakers]} for btts + draw_no_bet."""
    result = {}
    try:
        r = await client.get(
            f"https://api.the-odds-api.com/v4/sports/{sk}/events",
            params={"apiKey":ODDS_API_KEY,
                    "commenceTimeFrom":date_from,
                    "commenceTimeTo":date_to},
            timeout=10.0,
        )
        if r.status_code != 200:
            logger.warning(f"BTTS events {sk}: HTTP {r.status_code}")
            return result

        events = r.json()
        logger.info(f"  BTTS {sk}: fetching {min(len(events),12)} events")

        for ev in events[:12]:
            ev_id = ev.get("id")
            if not ev_id:
                continue
            try:
                ro = await client.get(
                    f"https://api.the-odds-api.com/v4/sports/{sk}/events/{ev_id}/odds",
                    params={"apiKey":ODDS_API_KEY,"regions":"eu,uk",
                            "markets":"btts,draw_no_bet","oddsFormat":"decimal"},
                    timeout=8.0,
                )
                if ro.status_code == 200:
                    key = (ev.get("home_team",""), ev.get("away_team",""))
                    result[key] = ro.json().get("bookmakers", [])
                await asyncio.sleep(0.08)
            except Exception as e:
                logger.debug(f"    BTTS event {ev_id}: {e}")

    except Exception as e:
        logger.error(f"fetch_btts {sk}: {e}")

    return result


# ─── MAIN FETCH ──────────────────────────────────────────────────────────────

async def fetch_all_matches() -> dict:
    logger.info("=== FETCH v7.2 ===")
    if not ODDS_API_KEY:
        logger.warning("No ODDS_API_KEY — aborting fetch")
        return {}

    date_from, date_to = today_window()
    logger.info(f"Window: {date_from} → {date_to}")

    async with httpx.AsyncClient(timeout=25.0) as client:

        # Step 1: get active sports list
        sr = await client.get(
            "https://api.the-odds-api.com/v4/sports",
            params={"apiKey": ODDS_API_KEY})
        if sr.status_code != 200:
            logger.error(f"Sports list HTTP {sr.status_code}")
            return {}

        sports = [s for s in sr.json() if s.get("active")]
        logger.info(f"Active sports: {len(sports)}")

        matches = {}
        total   = 0

        for sport in sports:
            sk   = sport.get("key", "")
            scat = sport_cat(sk)
            cfg  = SPORT_MARKETS[scat]
            if not sk:
                continue

            try:
                r = await client.get(
                    f"https://api.the-odds-api.com/v4/sports/{sk}/odds",
                    params={
                        "apiKey":           ODDS_API_KEY,
                        "regions":          "eu,uk",
                        "markets":          cfg["featured"],  # <── sport-specific
                        "commenceTimeFrom": date_from,
                        "commenceTimeTo":   date_to,
                        "oddsFormat":       "decimal",
                    },
                    timeout=12.0,
                )

                if r.status_code != 200:
                    if r.status_code != 422:
                        logger.warning(f"  {sk}: HTTP {r.status_code}")
                    continue

                events = r.json()
                if not events:
                    continue

                processed = []
                for ev in events:
                    home = ev.get("home_team", "")
                    away = ev.get("away_team", "")
                    if not home or not away:
                        continue
                    processed.append({
                        "id":         ev.get("id", ""),
                        "match":      f"{home} vs {away}",
                        "sport":      sk,
                        "scat":       scat,
                        "home":       home,
                        "away":       away,
                        "commence":   ev.get("commence_time", ""),
                        "bookmakers": ev.get("bookmakers", []),
                    })

                # Phase 2: BTTS only for soccer
                if scat == "soccer" and processed:
                    btts_map = await fetch_btts(client, sk, date_from, date_to)
                    for ev in processed:
                        key = (ev["home"], ev["away"])
                        if key in btts_map:
                            # merge additional bookmakers list
                            ev["bookmakers"] = ev["bookmakers"] + btts_map[key]

                matches[sk] = processed
                total += len(processed)

                # debug: log what markets actually came back
                sample_mks: set = set()
                for bk in (processed[0]["bookmakers"] if processed else [])[:3]:
                    for mk in bk.get("markets", []):
                        sample_mks.add(mk.get("key", ""))
                logger.info(
                    f"  [{scat}] {sk}: {len(processed)} events | "
                    f"requested={cfg['featured']} | returned={sample_mks}"
                )

            except Exception as e:
                logger.error(f"  {sk}: {e}")

    logger.info(f"=== FETCH DONE: {total} events, {len(matches)} sports ===")
    return matches


# ─── SCAN ────────────────────────────────────────────────────────────────────

async def scan():
    global ALL_MATCHES
    logger.info("=== SCAN v7.2 ===")

    if not ALL_MATCHES:
        await storage.tg("⚠️ No matches loaded. Run /refresh first.")
        return

    raw: list       = []
    total_outcomes  = 0
    mk_raw_counts   = {}
    sport_breakdown = {}

    for sk, events in ALL_MATCHES.items():
        scat   = sport_cat(sk)
        accept = SPORT_MARKETS[scat]["accept"]

        for event in events:
            bkms = event.get("bookmakers", [])
            if not bkms:
                continue
            seen: set = set()

            for bk in bkms[:4]:
                for market in bk.get("markets", []):
                    mk = market.get("key", "")
                    if mk not in accept:
                        continue

                    for out in market.get("outcomes", []):
                        name  = out.get("name", "")
                        odds  = out.get("price", 0.0)
                        point = out.get("point", None)
                        total_outcomes += 1
                        mk_raw_counts[mk] = mk_raw_counts.get(mk, 0) + 1

                        if not (MIN_ODDS <= odds <= MAX_ODDS):
                            continue

                        label     = market_label(mk, name, point)
                        dedup_key = (event["match"], label)
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)

                        conf = get_conf(mk, odds)
                        ev_  = calc_ev(odds, conf)
                        kf   = kelly_fraction(conf, odds)

                        if ev_ < MIN_EV or kf < MIN_KELLY:
                            continue

                        raw.append({
                            "match":  event["match"],
                            "sport":  sk,
                            "scat":   scat,
                            "mk":     mk,
                            "label":  label,
                            "odds":   odds,
                            "conf":   conf,
                            "ev":     ev_,
                            "kf":     kf,
                        })
                        d = sport_breakdown.setdefault(scat, {})
                        d[mk] = d.get(mk, 0) + 1

    logger.info(f"Outcomes scanned: {total_outcomes}")
    logger.info(f"By market (raw): {mk_raw_counts}")
    logger.info(f"After EV+Kelly filter: {len(raw)}")
    logger.info(f"Sport/market breakdown: {sport_breakdown}")

    if not raw:
        await storage.tg(
            f"⚠️ *SCAN: 0 value picks found*\n\n"
            f"Outcomes checked: {total_outcomes}\n"
            f"Markets seen: {mk_raw_counts}\n\n"
            f"Try `/refresh` to reload data.")
        return

    # Sort: non-h2h markets get a small bonus so they surface first
    picks = sorted(raw,
                   key=lambda p: p["ev"] + MK_BONUS.get(p["mk"], 0.0),
                   reverse=True)[:TOP_N]

    mk_final = {}
    for p in picks:
        mk_final[p["mk"]] = mk_final.get(p["mk"], 0) + 1
    logger.info(f"Sending {len(picks)} picks | {mk_final}")

    sent = 0
    for p in picks:
        emoji = MK_EMOJI.get(p["mk"], "🎯")
        rec   = "✅ ACCEPT"    if p["ev"] >= 0.15 else (
                "⚠️ CONSIDER" if p["ev"] >= 0.06 else "♻️ RECONSIDER")
        opt   = bet_size(storage.bank, p["kf"])
        msg   = (
            f"{rec} {emoji} *{p['sport'].upper()}*\n\n"
            f"*{p['match']}*\n"
            f"`{p['label']}`\n"
            f"Odds: `{p['odds']}` | EV: `{p['ev']:+.3f}`\n"
            f"Conf: {p['conf']:.0%} | Kelly: {p['kf']*100:.1f}%\n\n"
            f"💰 Optimal: *{opt:.0f} UAH*\n\n"
            f"/place\\_bet \"{p['match']}\" \"{p['label']}\" {p['odds']} {opt:.0f}"
        )
        await storage.tg(msg)
        await asyncio.sleep(0.15)
        sent += 1

    # summary
    summary = " | ".join(f"{k}:{v}" for k,v in sorted(mk_final.items()))
    await storage.tg(
        f"📊 *Scan done*\n"
        f"{sent} picks → {summary}")


# ─── COMMANDS ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *BETTING BOT v7.2*\n\n"
        "Market coverage:\n"
        "⚽ Soccer → h2h + spreads + totals + btts + dnb\n"
        "🏀 Basketball → h2h + spreads + totals + 1H\n"
        "🏒 Hockey → h2h + spreads + totals + P1/P2/P3\n"
        "🎾 Tennis → h2h only\n"
        "🥊 MMA → h2h only\n\n"
        f"Min odds: {MIN_ODDS} | Min EV: +{MIN_EV}\n"
        f"Bank: {storage.bank} UAH\n\n"
        "/scan /refresh /markets <team>\n"
        "/place_bet /bets /bank /stats /help",
        parse_mode="Markdown"
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Scanning all markets…")
    await scan()


async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ALL_MATCHES
    await update.message.reply_text("⏳ Reloading matches from API…")
    ALL_MATCHES = await fetch_all_matches()
    total  = sum(len(v) for v in ALL_MATCHES.values())
    sports = len(ALL_MATCHES)
    await update.message.reply_text(
        f"✅ Loaded *{total}* events in *{sports}* sports",
        parse_mode="Markdown"
    )


async def cmd_markets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug: show all available odds for a match."""
    q = " ".join(context.args).lower() if context.args else ""
    if not q:
        await update.message.reply_text(
            "Usage: /markets <team>\nExample: /markets Remo")
        return
    if not ALL_MATCHES:
        await update.message.reply_text("No matches. Use /refresh first.")
        return

    found = []
    for sk, events in ALL_MATCHES.items():
        for ev in events:
            if q in ev.get("home","").lower() or q in ev.get("away","").lower():
                found.append((sk, ev))

    if not found:
        await update.message.reply_text(f"No match found for '{q}'")
        return

    for sk, ev in found[:2]:
        scat   = sport_cat(sk)
        accept = SPORT_MARKETS[scat]["accept"]
        lines  = [f"🔍 *{ev['match']}*\n_{sk} [{scat}]_\n"]
        mk_map = {}

        for bk in ev.get("bookmakers", [])[:4]:
            for mk in bk.get("markets", []):
                k = mk.get("key", "")
                if k not in accept:
                    continue
                if k not in mk_map:
                    mk_map[k] = []
                for out in mk.get("outcomes", []):
                    pt = f" ({out['point']})" if out.get("point") is not None else ""
                    mk_map[k].append(f"  {out['name']}{pt}: `{out['price']}`")

        if not mk_map:
            lines.append("No odds in loaded data.\nTry /refresh first.")
        else:
            for k, rows in mk_map.items():
                lines.append(f"{MK_EMOJI.get(k,'🎯')} *{k}*")
                lines.extend(rows[:6])
                lines.append("")

        await update.message.reply_text(
            "\n".join(lines), parse_mode="Markdown")
        await asyncio.sleep(0.3)


async def cmd_place_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 4:
            await update.message.reply_text(
                'Usage: /place\\_bet "Match" "Market" odds stake')
            return
        match  = context.args[0]
        market = context.args[1]
        odds   = float(context.args[2])
        stake  = float(context.args[3])
        if odds < 1.0 or stake <= 0 or stake > storage.bank:
            await update.message.reply_text("Invalid odds or stake")
            return
        bet = storage.add_bet(match, market, odds, stake)
        await update.message.reply_text(
            f"✅ *Bet placed*\n"
            f"{match} | {market}\n"
            f"Odds: {odds} | Stake: {stake} UAH\n"
            f"Kelly: {bet['kelly']*100:.1f}% | Optimal: {bet['optimal_stake']:.0f} UAH",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_bets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    opens = [b for b in storage.bets if b.get("status") == "OPEN"]
    if not opens:
        await update.message.reply_text("No open bets.")
        return
    lines = ["📋 *OPEN BETS*\n"]
    for b in opens:
        lines.append(
            f"ID:{b['id']} | {b['match']}\n"
            f"{b['market']} @ {b['odds']} | {b['stake']} UAH\n")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = storage.stats()
    await update.message.reply_text(
        f"💰 *Bank:* {s['bank']:.2f} UAH\n"
        f"Profit: {s['profit']:+.2f} | Bets: {s['total']}\n"
        f"WR: {s['wr']:.1f}% | ROI: {s['roi']:.2f}%",
        parse_mode="Markdown"
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = storage.stats()
    await update.message.reply_text(
        f"Total: {s['total']} | Wins: {s['wins']} | Losses: {s['losses']}\n"
        f"WR: {s['wr']:.1f}% | Profit: {s['profit']:+.2f}\n"
        f"ROI: {s['roi']:.2f}%"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — info & market map\n"
        "/scan — scan for value picks\n"
        "/refresh — reload today's matches\n"
        "/markets <team> — debug all markets for a match\n"
        "/place_bet — log a bet\n"
        "/bets — open bets\n"
        "/bank — bankroll stats\n"
        "/stats — W/L/ROI\n"
        "/help — this message"
    )


# ─── SCHEDULER ───────────────────────────────────────────────────────────────

async def scheduled_refresh_scan():
    global ALL_MATCHES
    ALL_MATCHES = await fetch_all_matches()
    await scan()


# ─── INIT ────────────────────────────────────────────────────────────────────

async def post_init(app):
    global ALL_MATCHES
    storage.set_app(app)
    logger.info("=== BOT v7.2 INIT ===")

    ALL_MATCHES = await fetch_all_matches()

    if not scheduler.running:
        scheduler.start()
        scheduler.add_job(scan,                    "cron",
                          hour="*/1", minute=5, id="hourly_scan")
        scheduler.add_job(scheduled_refresh_scan,  "cron",
                          hour=7,     minute=0, id="morning_refresh")

    await scan()
    await storage.tg(
        "🤖 *Bot v7.2 ready*\n\n"
        "⚽ Soccer: h2h + spreads + totals + btts\n"
        "🏀 Basketball: h2h + spreads + totals + 1H\n"
        "🏒 Hockey: h2h + spreads + totals + P1/P2/P3\n"
        "🎾 Tennis: h2h\n"
        "🥊 MMA: h2h\n\n"
        f"Bank: {storage.bank} UAH | Min EV: +{MIN_EV}"
    )


async def post_stop(app):
    if scheduler.running:
        scheduler.shutdown()


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    app = (Application.builder()
           .token(TOKEN)
           .build())

    app.post_init = post_init
    app.post_stop = post_stop

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("scan",      cmd_scan))
    app.add_handler(CommandHandler("refresh",   cmd_refresh))
    app.add_handler(CommandHandler("markets",   cmd_markets))
    app.add_handler(CommandHandler("place_bet", cmd_place_bet))
    app.add_handler(CommandHandler("bets",      cmd_bets))
    app.add_handler(CommandHandler("bank",      cmd_bank))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CommandHandler("help",      cmd_help))

    app.run_polling()


if __name__ == "__main__":
    main()
