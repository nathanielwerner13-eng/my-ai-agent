import os
import json
import re
import base64
import requests
import threading
import time
import datetime
import uuid
from urllib.parse import urlencode
from email.mime.text import MIMEText
from flask import Flask, request, jsonify, session, redirect
from flask_cors import CORS
from anthropic import Anthropic
from duckduckgo_search import DDGS
from pywebpush import webpush, WebPushException
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from pinecone import Pinecone

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get('SECRET_KEY', 'bina-secret-key-2024')
CORS(app)

client = Anthropic()
pc = Pinecone(api_key=os.environ.get('PINECONE_API_KEY', ''))
PINECONE_INDEX = os.environ.get('PINECONE_INDEX', 'bina-memory')

NOTIFICATIONS_FILE = 'notifications.json'
SEEN_EMAILS_FILE = 'seen_emails.json'
SUBSCRIPTIONS_FILE = 'subscriptions.json'
OVERNIGHT_REPORT_FILE = 'overnight_report.json'
GMAIL_SCOPES = 'https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/calendar'
REDIRECT_URI = 'https://my-ai-agent-production-5e17.up.railway.app/oauth/callback'
VAPID_PUBLIC_KEY = os.environ.get('VAPID_PUBLIC_KEY', '')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', '')
VAPID_CLAIMS_EMAIL = os.environ.get('VAPID_CLAIMS_EMAIL', 'mailto:nathanielwerner13@gmail.com')
BINA_URL = 'https://my-ai-agent-production-5e17.up.railway.app'
SERPER_API_KEY = os.environ.get('SERPER_API_KEY', '')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
ALPHA_VANTAGE_KEY = os.environ.get('ALPHA_VANTAGE_KEY', '')
FRED_API_KEY = os.environ.get('FRED_API_KEY', '')

SYSTEM_PROMPT = """You are Bina (בינה), a fully autonomous AI agent and personal chief of staff for Nathaniel Werner.

ABOUT NATHANIEL:
- 18 years old, college student in Beverly Hills
- Entrepreneur focused on building autonomous income systems
- Interested in crypto, investments, Polymarket, Kalshi, TikTok/content, business automation
- Watches: Bitcoin, Ethereum, Solana, Chainlink, Render
- Interested in US politics, world politics, prediction markets, weather markets, commodities
- Building toward financial freedom and passive income
- Direct, ambitious, moves fast, hates wasted time
- Jewish background (uses Hebrew greetings occasionally)

YOUR ROLE:
You are Nathaniel's personal chief of staff, business partner, and autonomous agent. You think ahead, spot opportunities, and help him execute fast.

YOUR CAPABILITIES:
- Send emails: SEND_EMAIL|to@email.com|Subject|Body END_EMAIL
- Create calendar events: CREATE_EVENT|Title|2026-05-17T10:00:00|2026-05-17T11:00:00|Description END_EVENT
- Deep web search, persistent memory, live crypto/Polymarket/Kalshi/commodities/economic data

CRITICAL MEMORY INSTRUCTIONS:
Reference memories naturally. Never ignore relevant memories.

PERSONALITY:
- Sharp, direct, no fluff
- Write like a smart friend texting — not a formal report
- Be concise unless depth is needed
- Only recommend positions when you have verified real data and understand contract mechanics

The current date and time in Los Angeles will be injected into every message."""


# ── Memory ────────────────────────────────────────────────────────────────────

def get_embedding(text):
    try:
        response = requests.post(
            'https://api.openai.com/v1/embeddings',
            headers={'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'},
            json={'model': 'text-embedding-ada-002', 'input': text[:8000]}
        )
        if response.status_code == 200:
            return response.json()['data'][0]['embedding']
        return None
    except Exception as e:
        print(f"Embedding error: {str(e)}")
        return None

def save_memory(text, memory_type='conversation', metadata=None):
    try:
        embedding = get_embedding(text)
        if not embedding:
            return False
        index = pc.Index(PINECONE_INDEX)
        memory_id = str(uuid.uuid4())
        meta = {
            'text': text[:1000],
            'type': memory_type,
            'timestamp': time.time(),
            'date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        }
        if metadata:
            meta.update(metadata)
        index.upsert(vectors=[{'id': memory_id, 'values': embedding, 'metadata': meta}])
        return True
    except Exception as e:
        print(f"Save memory error: {str(e)}")
        return False

def search_memories(query, top_k=8, threshold=0.5):
    try:
        embedding = get_embedding(query)
        if not embedding:
            return []
        index = pc.Index(PINECONE_INDEX)
        results = index.query(vector=embedding, top_k=top_k, include_metadata=True)
        memories = []
        for match in results.matches:
            if match.score > threshold:
                memories.append({
                    'text': match.metadata.get('text', ''),
                    'date': match.metadata.get('date', ''),
                    'type': match.metadata.get('type', ''),
                    'score': match.score
                })
        return memories
    except Exception as e:
        print(f"Search memory error: {str(e)}")
        return []

def get_all_context_memories(user_message):
    msg_memories = search_memories(user_message, top_k=8, threshold=0.5)
    personal_memories = search_memories("Nathaniel family mother father friends personal life", top_k=5, threshold=0.4)
    business_memories = search_memories("Nathaniel business goals investments ideas plans", top_k=5, threshold=0.4)
    seen_texts = set()
    all_memories = []
    for m in msg_memories + personal_memories + business_memories:
        if m['text'] not in seen_texts:
            seen_texts.add(m['text'])
            all_memories.append(m)
    all_memories.sort(key=lambda x: x['score'], reverse=True)
    return all_memories[:12]

def format_memories(memories):
    if not memories:
        return ""
    output = "\n\nWhat you remember about Nathaniel:\n"
    for m in memories:
        output += f"• [{m['date']}] {m['text']}\n"
    return output


# ── Push ──────────────────────────────────────────────────────────────────────

def load_subscriptions():
    if os.path.exists(SUBSCRIPTIONS_FILE):
        with open(SUBSCRIPTIONS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_subscriptions(subs):
    with open(SUBSCRIPTIONS_FILE, 'w') as f:
        json.dump(subs, f)

def send_push(title, body, url=None, notif_type='feed'):
    if url is None:
        url = BINA_URL
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        return
    subscriptions = load_subscriptions()
    if not subscriptions:
        return
    payload = json.dumps({'title': title, 'body': body, 'url': url, 'type': notif_type})
    good_subs = []
    for sub in subscriptions:
        try:
            webpush(subscription_info=sub, data=payload,
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims={'sub': VAPID_CLAIMS_EMAIL})
            good_subs.append(sub)
        except WebPushException as e:
            if '400' not in str(e) and '410' not in str(e):
                good_subs.append(sub)
    save_subscriptions(good_subs)


# ── Notifications ─────────────────────────────────────────────────────────────

def load_notifications():
    if os.path.exists(NOTIFICATIONS_FILE):
        with open(NOTIFICATIONS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_notifications(notifications):
    with open(NOTIFICATIONS_FILE, 'w') as f:
        json.dump(notifications, f)

def add_notification(notif):
    notifications = load_notifications()
    notifications.insert(0, notif)
    notifications = notifications[:50]
    save_notifications(notifications)


# ── Seen Emails ───────────────────────────────────────────────────────────────

def load_seen_emails():
    if os.path.exists(SEEN_EMAILS_FILE):
        with open(SEEN_EMAILS_FILE, 'r') as f:
            return set(json.load(f))
    return set()

def save_seen_emails(seen):
    with open(SEEN_EMAILS_FILE, 'w') as f:
        json.dump(list(seen), f)


# ── Junk Filter ───────────────────────────────────────────────────────────────

def is_important_email(email):
    junk_keywords = [
        'noreply', 'no-reply', 'donotreply', 'newsletter', 'notifications@',
        'mailer', 'updates@', 'info@', 'support@', 'hello@', 'team@',
        'news@', 'digest', 'unsubscribe', 'marketing', 'promo', 'offer',
        'notification', 'alert@', 'automated', 'bounce', 'postmaster',
        'do-not-reply', 'system@', 'admin@', 'billing@', 'invoice@',
        'receipt@', 'confirm', 'verify', 'activate', 'password',
        'security alert', 'sign-in', 'signin', 'account activity',
        'canvas@', 'canvasemail', 'instructure', 'campusdpp'
    ]
    sender = email['from'].lower()
    subject = email['subject'].lower()
    for word in junk_keywords:
        if word in sender or word in subject:
            return False
    return True


# ── Web Search ────────────────────────────────────────────────────────────────

def web_search(query, num_results=5):
    if SERPER_API_KEY:
        try:
            response = requests.post(
                'https://google.serper.dev/search',
                headers={'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'},
                json={'q': query, 'num': num_results}
            )
            data = response.json()
            output = f"Search: '{query}':\n"
            if data.get('answerBox'):
                answer = data['answerBox'].get('answer') or data['answerBox'].get('snippet') or ''
                if answer:
                    output += f"Answer: {answer}\n"
            for r in data.get('organic', [])[:num_results]:
                output += f"• {r.get('title', '')}: {r.get('snippet', '')}\n"
            for n in data.get('news', [])[:3]:
                output += f"• NEWS: {n.get('title', '')} ({n.get('date', '')}): {n.get('snippet', '')}\n"
            return output
        except Exception as e:
            print(f"Serper error: {str(e)}")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=num_results))
            return "\n".join([f"• {r['title']}: {r['body']}" for r in results])
    except Exception as e:
        return f"Search error: {str(e)}"


# ── KALSHI (public API, no key needed) ───────────────────────────────────────

def get_kalshi_markets(limit=50):
    """Pull Kalshi markets — free public API, no auth needed for reading."""
    try:
        response = requests.get(
            'https://api.elections.kalshi.com/trade-api/v2/markets',
            params={'limit': limit, 'status': 'open'},
            headers={'Accept': 'application/json'},
            timeout=15
        )
        if response.status_code == 200:
            data = response.json()
            markets = data.get('markets', [])
            processed = []
            now_utc = datetime.datetime.now(datetime.timezone.utc)

            for m in markets:
                close_time = m.get('close_time', '')
                days_until = None
                end_date_str = 'Unknown'
                if close_time:
                    try:
                        end_dt = datetime.datetime.fromisoformat(close_time.replace('Z', '+00:00'))
                        days_until = (end_dt - now_utc).days
                        end_date_str = end_dt.strftime('%b %d, %Y')
                        if days_until < 0:
                            continue
                    except:
                        pass

                yes_price = m.get('yes_bid', m.get('last_price', None))
                if yes_price is not None:
                    yes_price = yes_price / 100  # Kalshi uses cents

                volume = m.get('volume', 0) or 0

                processed.append({
                    'title': m.get('title', ''),
                    'subtitle': m.get('subtitle', ''),
                    'yes_price': yes_price,
                    'no_price': (1 - yes_price) if yes_price else None,
                    'volume': volume,
                    'end_date': end_date_str,
                    'days_until': days_until,
                    'category': m.get('category', ''),
                    'ticker': m.get('ticker_name', '')
                })

            return processed
        print(f"Kalshi API status: {response.status_code}")
        return []
    except Exception as e:
        print(f"Kalshi error: {str(e)}")
        return []

def cross_reference_markets(polymarket_markets, kalshi_markets):
    """Find same events priced differently on Polymarket vs Kalshi."""
    if not polymarket_markets or not kalshi_markets:
        return ""

    output = "\n**POLYMARKET vs KALSHI CROSS-REFERENCE:**\n"
    discrepancies = []

    # Keywords to match similar markets
    for pm in polymarket_markets[:30]:
        pm_title = pm['title'].lower()
        pm_yes = pm.get('yes_price')
        if not pm_yes:
            continue

        for km in kalshi_markets[:50]:
            km_title = (km['title'] + ' ' + km.get('subtitle', '')).lower()
            km_yes = km.get('yes_price')
            if not km_yes:
                continue

            # Check for keyword overlap
            pm_words = set(pm_title.split())
            km_words = set(km_title.split())
            overlap = pm_words & km_words
            significant_overlap = [w for w in overlap if len(w) > 4]

            if len(significant_overlap) >= 2:
                diff = abs(pm_yes - km_yes)
                if diff > 0.05:  # Only flag if >5% difference
                    discrepancies.append({
                        'pm_title': pm['title'],
                        'km_title': km['title'],
                        'pm_yes': pm_yes,
                        'km_yes': km_yes,
                        'diff': diff,
                        'pm_end': pm.get('end_date', '?'),
                        'km_end': km.get('end_date', '?')
                    })

    if discrepancies:
        discrepancies.sort(key=lambda x: x['diff'], reverse=True)
        output += f"Found {len(discrepancies)} potential discrepancies:\n"
        for d in discrepancies[:5]:
            direction = "Polymarket HIGHER" if d['pm_yes'] > d['km_yes'] else "Kalshi HIGHER"
            output += f"\n⚡ PRICE GAP: {d['diff']:.1%} — {direction}\n"
            output += f"  Polymarket: '{d['pm_title']}' → YES {d['pm_yes']:.1%} (resolves {d['pm_end']})\n"
            output += f"  Kalshi:     '{d['km_title']}' → YES {d['km_yes']:.1%} (resolves {d['km_end']})\n"
    else:
        output += "No significant price gaps found between platforms.\n"

    return output

def format_kalshi_markets(markets):
    """Format top Kalshi markets by category."""
    if not markets:
        return "Kalshi data unavailable."

    output = "**Kalshi Markets (Top by Volume)**\n"
    by_category = {}
    for m in markets:
        cat = m.get('category', 'Other') or 'Other'
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(m)

    priority_cats = ['Politics', 'Economics', 'Climate', 'Crypto', 'Sports']
    for cat in priority_cats:
        if cat in by_category:
            output += f"\n{cat.upper()}:\n"
            sorted_markets = sorted(by_category[cat], key=lambda x: x.get('volume', 0), reverse=True)
            for m in sorted_markets[:4]:
                yes = m.get('yes_price')
                vol = m.get('volume', 0)
                end = m.get('end_date', '?')
                days = m.get('days_until')
                days_str = f"{days}d" if days is not None else "?"
                if yes:
                    output += f"• {m['title']}\n  YES: {yes:.1%} | Vol: {vol:,} | Resolves: {end} ({days_str})\n"

    return output


# ── METACULUS (free, no key needed) ──────────────────────────────────────────

def get_metaculus_questions(limit=20):
    """Pull top forecaster predictions from Metaculus — completely free."""
    try:
        response = requests.get(
            'https://www.metaculus.com/api2/questions/',
            params={
                'limit': limit,
                'status': 'open',
                'order_by': '-activity',
                'type': 'forecast'
            },
            headers={'Accept': 'application/json'},
            timeout=15
        )
        if response.status_code == 200:
            data = response.json()
            questions = []
            for q in data.get('results', []):
                community = q.get('community_prediction', {})
                prediction = None
                if community:
                    prediction = community.get('full', {}).get('q2')

                questions.append({
                    'title': q.get('title', ''),
                    'prediction': prediction,
                    'resolution_date': q.get('resolution', '')[:10] if q.get('resolution') else 'Unknown',
                    'forecasters': q.get('number_of_predictions', 0),
                    'url': f"https://metaculus.com/questions/{q.get('id', '')}"
                })
            return questions
        return []
    except Exception as e:
        print(f"Metaculus error: {str(e)}")
        return []

def format_metaculus(questions):
    """Format Metaculus predictions — these are expert forecasters, not money traders."""
    if not questions:
        return "Metaculus data unavailable."

    output = "**Metaculus Expert Forecasts** (community of superforecasters)\n"
    for q in questions[:8]:
        pred = q.get('prediction')
        forecasters = q.get('forecasters', 0)
        res_date = q.get('resolution_date', '?')
        if pred is not None:
            output += f"• {q['title']}\n  Forecast: **{pred:.1%}** | {forecasters} forecasters | Resolves: {res_date}\n"
        else:
            output += f"• {q['title']} | Resolves: {res_date}\n"
    return output

def cross_reference_metaculus_polymarket(metaculus_questions, polymarket_markets):
    """Find where expert forecasters disagree with Polymarket money."""
    if not metaculus_questions or not polymarket_markets:
        return ""

    output = "\n**METACULUS vs POLYMARKET — Expert vs Money:**\n"
    gaps = []

    for mq in metaculus_questions:
        mq_title = mq['title'].lower()
        mq_pred = mq.get('prediction')
        if not mq_pred:
            continue

        for pm in polymarket_markets[:30]:
            pm_title = pm['title'].lower()
            pm_yes = pm.get('yes_price')
            if not pm_yes:
                continue

            mq_words = set(mq_title.split())
            pm_words = set(pm_title.split())
            overlap = [w for w in (mq_words & pm_words) if len(w) > 4]

            if len(overlap) >= 2:
                diff = abs(mq_pred - pm_yes)
                if diff > 0.08:
                    gaps.append({
                        'mq_title': mq['title'],
                        'pm_title': pm['title'],
                        'mq_pred': mq_pred,
                        'pm_yes': pm_yes,
                        'diff': diff,
                        'forecasters': mq.get('forecasters', 0),
                        'direction': 'Experts HIGHER' if mq_pred > pm_yes else 'Market HIGHER'
                    })

    if gaps:
        gaps.sort(key=lambda x: x['diff'], reverse=True)
        output += f"Found {len(gaps)} expert/market disagreements:\n"
        for g in gaps[:3]:
            output += f"\n⚡ {g['diff']:.1%} gap — {g['direction']}\n"
            output += f"  Metaculus ({g['forecasters']} forecasters): {g['mq_pred']:.1%} — '{g['mq_title']}'\n"
            output += f"  Polymarket money: {g['pm_yes']:.1%} — '{g['pm_title']}'\n"
    else:
        output += "No significant expert/market disagreements found.\n"

    return output


# ── FRED Economic Data (free, just needs key) ─────────────────────────────────

def get_fred_data():
    """Get real economic indicators from Federal Reserve."""
    if not FRED_API_KEY:
        return "FRED API key not configured — add FRED_API_KEY to Railway."

    indicators = {
        'FEDFUNDS': 'Fed Funds Rate',
        'CPIAUCSL': 'CPI Inflation',
        'UNRATE': 'Unemployment Rate',
        'DGS10': '10-Year Treasury Yield',
        'DCOILWTICO': 'WTI Oil Price',
        'GOLDAMGBD228NLBM': 'Gold Price (London Fix)'
    }

    output = "**FRED Economic Indicators (Federal Reserve Data)**\n"
    for series_id, name in indicators.items():
        try:
            response = requests.get(
                'https://api.stlouisfed.org/fred/series/observations',
                params={
                    'series_id': series_id,
                    'api_key': FRED_API_KEY,
                    'file_type': 'json',
                    'limit': 1,
                    'sort_order': 'desc'
                },
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                obs = data.get('observations', [])
                if obs:
                    value = obs[0].get('value', 'N/A')
                    date = obs[0].get('date', '')
                    if value != '.':
                        output += f"• **{name}**: {value} (as of {date})\n"
            time.sleep(0.3)
        except Exception as e:
            output += f"• {name}: unavailable\n"

    return output


# ── Real Commodity Prices via Alpha Vantage ───────────────────────────────────

def get_real_commodity_prices():
    output = "**Commodities**\n"

    if ALPHA_VANTAGE_KEY:
        try:
            gold_response = requests.get(
                'https://www.alphavantage.co/query',
                params={
                    'function': 'CURRENCY_EXCHANGE_RATE',
                    'from_currency': 'XAU',
                    'to_currency': 'USD',
                    'apikey': ALPHA_VANTAGE_KEY
                },
                timeout=10
            )
            if gold_response.status_code == 200:
                rate = gold_response.json().get('Realtime Currency Exchange Rate', {})
                gold_price = rate.get('5. Exchange Rate', None)
                if gold_price:
                    output += f"🥇 **Gold**: ${float(gold_price):,.2f}/oz\n"
                else:
                    output += "🥇 Gold: unavailable\n"
        except Exception as e:
            output += f"🥇 Gold: error\n"

        try:
            oil_response = requests.get(
                'https://www.alphavantage.co/query',
                params={
                    'function': 'BRENT',
                    'interval': 'daily',
                    'apikey': ALPHA_VANTAGE_KEY
                },
                timeout=10
            )
            if oil_response.status_code == 200:
                data = oil_response.json()
                series = data.get('data', [])
                if series:
                    latest = series[0]
                    output += f"🛢️ **Brent Oil**: ${float(latest.get('value', 0)):,.2f}/barrel (as of {latest.get('date', '?')})\n"
                else:
                    output += "🛢️ Oil: unavailable\n"
        except Exception as e:
            output += f"🛢️ Oil: error\n"
    else:
        gold = web_search("gold spot price per ounce USD today", num_results=1)
        oil = web_search("WTI crude oil price per barrel USD today", num_results=1)
        output += f"🥇 Gold (search): {gold[:150]}\n"
        output += f"🛢️ Oil (search): {oil[:150]}\n"

    return output


# ── Polymarket with REAL ODDS + CONTRACT MECHANICS ────────────────────────────

def get_polymarket_markets_with_odds(limit=100):
    try:
        response = requests.get(
            'https://gamma-api.polymarket.com/markets',
            params={'limit': limit, 'active': 'true', 'closed': 'false'},
            timeout=15
        )
        if response.status_code != 200:
            return []

        markets = response.json()
        processed = []
        now_utc = datetime.datetime.now(datetime.timezone.utc)

        for m in markets:
            title = m.get('question', m.get('title', ''))
            volume = m.get('volume', 0) or 0
            try:
                volume = float(str(volume).replace(',', ''))
            except:
                volume = 0

            yes_price = None
            no_price = None
            try:
                outcome_prices = m.get('outcomePrices', '[]')
                if isinstance(outcome_prices, str):
                    prices = json.loads(outcome_prices)
                elif isinstance(outcome_prices, list):
                    prices = outcome_prices
                else:
                    prices = []
                if len(prices) >= 2:
                    yes_price = float(prices[0])
                    no_price = float(prices[1])
                elif len(prices) == 1:
                    yes_price = float(prices[0])
                    no_price = 1 - yes_price
            except:
                pass

            end_date = m.get('endDate', m.get('end_date_iso', ''))
            days_until = None
            end_date_str = 'Unknown'
            is_expired = False

            if end_date:
                try:
                    end_dt = datetime.datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                    days_until = (end_dt - now_utc).days
                    end_date_str = end_dt.strftime('%b %d, %Y')
                    if days_until < 0:
                        is_expired = True
                except:
                    end_date_str = str(end_date)[:10]

            if is_expired:
                continue

            description = m.get('description', '')[:400] if m.get('description') else ''

            has_floor = False
            floor_description = ''
            if description and any(phrase in description.lower() for phrase in
                   ['50-50', '50/50', 'neither', 'split evenly', 'resolve 0.5',
                    'resolve at 0.5', 'push', 'no contest', '0.50']):
                has_floor = True
                floor_description = 'CAPITAL TRAP — resolves 50/50 if neither event happens by deadline'

            skip = False
            if yes_price is not None and (yes_price > 0.92 or yes_price < 0.08):
                skip = True

            processed.append({
                'title': title,
                'volume': volume,
                'yes_price': yes_price,
                'no_price': no_price,
                'end_date': end_date_str,
                'days_until_resolution': days_until,
                'description': description,
                'has_floor': has_floor,
                'floor_description': floor_description,
                'skip': skip,
                'tags': [t.get('label', '') if isinstance(t, dict) else str(t)
                         for t in (m.get('tags') or [])],
            })

        return processed
    except Exception as e:
        print(f"Polymarket error: {str(e)}")
        return []


def categorize_polymarket(markets):
    political = []
    crypto_markets = []
    weather = []
    economics = []
    sports = []

    SPORTS_KEYWORDS = [
        'nhl', 'nba', 'nfl', 'mlb', 'mls', 'ufc', 'fifa', 'world cup',
        'stanley cup', 'super bowl', 'championship', 'playoffs',
        'golden knights', 'hurricanes', 'rangers', 'oilers', 'panthers',
        'avalanche', 'spurs', 'lakers', 'celtics', 'warriors', 'heat',
        'knicks', 'nuggets', 'patriots', 'chiefs', 'eagles', 'cowboys',
        'bills', '49ers', 'yankees', 'dodgers', 'astros', 'braves', 'mets',
        'tennis', 'golf', 'formula 1', 'f1', 'boxing', 'wrestling',
        'wimbledon', 'us open', 'masters', 'olympics', 'premier league',
        'la liga', 'serie a', 'bundesliga', 'champions league',
        'win the series', 'win the finals', 'win the cup', 'win the bowl',
        'nba finals', 'france win', 'spain win', 'england win',
        'brazil win', 'argentina win', 'portugal win', 'germany win'
    ]

    for m in markets:
        if m.get('skip'):
            continue
        title = m['title'].lower()
        tags = [t.lower() for t in m.get('tags', [])]
        all_text = title + ' ' + ' '.join(tags)

        if any(w in all_text for w in SPORTS_KEYWORDS):
            sports.append(m)
        elif any(w in all_text for w in [
                'weather', 'temperature', 'rain', 'snow', 'hurricane named',
                'tornado', 'flood', 'storm', 'celsius', 'fahrenheit',
                'precipitation', 'drought', 'wildfire', 'earthquake',
                'typhoon', 'blizzard', 'inches of', 'degrees']):
            weather.append(m)
        elif any(w in all_text for w in [
                'bitcoin', 'btc', 'eth', 'ethereum', 'crypto', 'solana',
                'coin', 'token', 'defi', 'nft', 'blockchain', 'web3',
                'altcoin', 'binance', 'coinbase', 'airdrop']):
            crypto_markets.append(m)
        elif any(w in all_text for w in [
                'fed', 'federal reserve', 'interest rate', 'gdp', 'inflation',
                'recession', 'oil price', 'gold price', 'stock market',
                'nasdaq', 's&p', 'dow jones', 'unemployment', 'cpi',
                'jobs report', 'treasury', 'yield', 'tariff', 'trade deal']):
            economics.append(m)
        elif any(w in all_text for w in [
                'president', 'election', 'congress', 'senate', 'trump', 'biden',
                'democrat', 'republican', 'war', 'nato', 'ukraine', 'china',
                'iran', 'israel', 'vote', 'prime minister', 'geopolit',
                'policy', 'government', 'minister', 'parliament', 'military',
                'sanction', 'ceasefire', 'invasion', 'coup', 'gta vi',
                'before gta']):
            political.append(m)

    for lst in [political, crypto_markets, weather, economics, sports]:
        lst.sort(key=lambda x: x['volume'], reverse=True)

    return {
        'political': political[:15],
        'crypto': crypto_markets[:10],
        'weather': weather[:10],
        'economics': economics[:10],
        'sports': sports[:10]
    }


def calculate_sports_arb(sports_markets):
    output = ""
    grouped = {}
    for m in sports_markets:
        end = m.get('end_date', 'Unknown')
        if end not in grouped:
            grouped[end] = []
        grouped[end].append(m)

    for end_date, group in grouped.items():
        if len(group) < 3:
            continue
        total_yes = sum(m.get('yes_price', 0) for m in group if m.get('yes_price'))
        days = group[0].get('days_until_resolution', 0)
        if total_yes < 0.95 and len(group) >= 4:
            gap = 1.0 - total_yes
            output += f"\n🎯 ARB OPPORTUNITY — {end_date} ({days}d left)\n"
            output += f"Total YES: {total_yes:.1%} across {len(group)} outcomes (gap: {gap:.1%})\n"
            for m in sorted(group, key=lambda x: x.get('yes_price', 0), reverse=True)[:6]:
                output += f"  • {m['title']}: YES {m.get('yes_price', 0):.1%}\n"
            if gap > 0.05:
                output += f"  ⚡ Buy the field — {gap:.1%} expected value gap\n"
    return output


def format_markets_for_analysis(categorized):
    output = ""

    def format_category(name, markets):
        if not markets:
            return ""
        result = f"\n{name.upper()} MARKETS:\n"
        for m in markets[:8]:
            yes = m.get('yes_price')
            no = m.get('no_price')
            vol = m.get('volume', 0)
            end = m.get('end_date', 'Unknown')
            days = m.get('days_until_resolution')
            has_floor = m.get('has_floor', False)
            floor_desc = m.get('floor_description', '')
            days_str = f"{days}d left" if days is not None else "?"

            if yes is not None:
                result += f"• {m['title']}\n"
                result += f"  YES: {yes:.1%} | NO: {no:.1%} | Vol: ${vol:,.0f} | Resolves: {end} ({days_str})\n"
                if has_floor:
                    result += f"  ⚠️ {floor_desc}\n"
                if m.get('description'):
                    result += f"  Rules: {m['description'][:100]}\n"
            else:
                result += f"• {m['title']} | Vol: ${vol:,.0f} | Resolves: {end} ({days_str})\n"
        return result

    output += format_category("Political", categorized['political'])
    output += format_category("Crypto Prediction", categorized['crypto'])
    output += format_category("Weather", categorized['weather'])
    output += format_category("Economic", categorized['economics'])
    output += format_category("Sports", categorized['sports'])

    arb = calculate_sports_arb(categorized.get('sports', []))
    if arb:
        output += f"\nSPORTS ARB ANALYSIS:\n{arb}"

    return output


# ── Crypto ────────────────────────────────────────────────────────────────────

def get_crypto_data():
    try:
        response = requests.get(
            'https://api.coingecko.com/api/v3/simple/price',
            params={
                'ids': 'bitcoin,ethereum,solana,chainlink,render-token',
                'vs_currencies': 'usd',
                'include_24hr_change': 'true',
                'include_24hr_vol': 'true',
                'include_market_cap': 'true'
            },
            timeout=10
        )
        if response.status_code == 200:
            return response.json()
        return {}
    except:
        return {}

def get_fear_greed_index():
    try:
        response = requests.get('https://api.alternative.me/fng/', timeout=10)
        if response.status_code == 200:
            return response.json()['data'][0]
        return {}
    except:
        return {}

def format_crypto_report(crypto_data, fear_greed):
    if not crypto_data:
        return "Crypto data unavailable."
    coin_names = {
        'bitcoin': 'Bitcoin (BTC)',
        'ethereum': 'Ethereum (ETH)',
        'solana': 'Solana (SOL)',
        'chainlink': 'Chainlink (LINK)',
        'render-token': 'Render (RNDR)'
    }
    output = "**Crypto Prices**\n"
    if fear_greed:
        output += f"Fear & Greed: **{fear_greed.get('value')} — {fear_greed.get('value_classification')}**\n\n"
    for coin_id, name in coin_names.items():
        if coin_id in crypto_data:
            d = crypto_data[coin_id]
            price = d.get('usd', 0)
            change = d.get('usd_24h_change', 0) or 0
            arrow = '📈' if change > 0 else '📉'
            output += f"{arrow} **{name}**: ${price:,.2f} | {change:+.2f}% 24h\n"
    return output


# ── Political Research ────────────────────────────────────────────────────────

def research_political_news():
    queries = [
        "US politics breaking news today",
        "world politics major events today",
        "Federal Reserve interest rate decision news",
        "Trump policy announcement today",
        "China US trade geopolitical news today",
        "Middle East war news today",
        "Ukraine Russia war update today",
        "economic data inflation report today",
        "2026 midterm election news today",
        "2028 presidential race news today"
    ]
    all_results = ""
    for query in queries:
        result = web_search(query, num_results=2)
        all_results += f"[{query}]:\n{result}\n\n"
        time.sleep(1)
    return all_results


# ── Sports Research ───────────────────────────────────────────────────────────

def research_sports_markets(sports_markets):
    if not sports_markets:
        return "No sports markets."
    output = "**Sports Markets — Verified Data**\n"
    for m in sports_markets[:4]:
        title = m['title']
        yes = m.get('yes_price')
        end = m.get('end_date', 'Unknown')
        days = m.get('days_until_resolution')
        result = web_search(f"{title} current odds standings 2026", num_results=3)
        odds_str = f"YES: {yes:.1%}" if yes else "odds N/A"
        days_str = f"{days}d left" if days is not None else "?"
        output += f"\n• {title}\n  Polymarket: {odds_str} | Resolves: {end} ({days_str})\n  Real data: {result[:400]}\n"
        time.sleep(1)
    return output


# ── Weather Research ──────────────────────────────────────────────────────────

def research_weather_for_markets(weather_markets):
    if not weather_markets:
        return "No weather markets found."
    output = "**Weather Market Research**\n"
    for m in weather_markets[:5]:
        title = m['title']
        yes = m.get('yes_price')
        no = m.get('no_price')
        end = m.get('end_date', 'Unknown')
        days = m.get('days_until_resolution')
        result = web_search(f"weather forecast {title} current conditions", num_results=2)
        odds_str = f"YES: {yes:.1%} | NO: {no:.1%}" if yes else "odds N/A"
        days_str = f"{days}d left" if days is not None else "?"
        output += f"\n• {title}\n  Odds: {odds_str} | Resolves: {end} ({days_str})\n  Forecast: {result[:300]}\n"
        time.sleep(0.5)
    return output


# ── Overnight Research ────────────────────────────────────────────────────────

def run_overnight_research():
    print("Starting overnight research...")
    report = {
        'date': datetime.datetime.now().strftime('%Y-%m-%d'),
        'time': datetime.datetime.now().strftime('%H:%M'),
        'polymarket': '',
        'kalshi': '',
        'cross_reference': '',
        'metaculus': '',
        'metaculus_vs_poly': '',
        'crypto': '',
        'political': '',
        'weather': '',
        'commodities': '',
        'fred': '',
        'sports': '',
        'synthesis': ''
    }

    # Polymarket
    print("Scanning Polymarket...")
    try:
        markets = get_polymarket_markets_with_odds(limit=100)
        categorized = categorize_polymarket(markets)
        poly_formatted = format_markets_for_analysis(categorized)
        report['polymarket'] = poly_formatted
        report['weather_markets'] = categorized.get('weather', [])
        report['sports_markets'] = categorized.get('sports', [])
        report['all_poly_markets'] = markets
        total = sum(len(v) for v in categorized.values() if isinstance(v, list))
        print(f"Polymarket: {total} markets")
    except Exception as e:
        report['polymarket'] = f"Polymarket unavailable: {str(e)}"

    time.sleep(5)

    # Kalshi
    print("Scanning Kalshi...")
    try:
        kalshi_markets = get_kalshi_markets(limit=100)
        report['kalshi'] = format_kalshi_markets(kalshi_markets)
        report['kalshi_raw'] = kalshi_markets

        # Cross-reference with Polymarket
        all_poly = report.get('all_poly_markets', [])
        if all_poly and kalshi_markets:
            report['cross_reference'] = cross_reference_markets(all_poly, kalshi_markets)
        print(f"Kalshi: {len(kalshi_markets)} markets")
    except Exception as e:
        report['kalshi'] = f"Kalshi unavailable: {str(e)}"
        print(f"Kalshi error: {str(e)}")

    time.sleep(5)

    # Metaculus
    print("Getting Metaculus forecasts...")
    try:
        metaculus_questions = get_metaculus_questions(limit=20)
        report['metaculus'] = format_metaculus(metaculus_questions)
        all_poly = report.get('all_poly_markets', [])
        if all_poly and metaculus_questions:
            report['metaculus_vs_poly'] = cross_reference_metaculus_polymarket(metaculus_questions, all_poly)
        print(f"Metaculus: {len(metaculus_questions)} questions")
    except Exception as e:
        report['metaculus'] = f"Metaculus unavailable: {str(e)}"
        print(f"Metaculus error: {str(e)}")

    time.sleep(5)

    # Crypto
    print("Scanning crypto...")
    try:
        crypto_data = get_crypto_data()
        fear_greed = get_fear_greed_index()
        report['crypto'] = format_crypto_report(crypto_data, fear_greed)
        print("Crypto done")
    except Exception as e:
        report['crypto'] = f"Crypto unavailable: {str(e)}"

    time.sleep(5)

    # FRED economic data
    print("Getting FRED economic data...")
    try:
        report['fred'] = get_fred_data()
        print("FRED done")
    except Exception as e:
        report['fred'] = f"FRED unavailable: {str(e)}"

    time.sleep(5)

    # Commodities
    print("Getting commodities...")
    try:
        report['commodities'] = get_real_commodity_prices()
        print("Commodities done")
    except Exception as e:
        report['commodities'] = f"Commodities unavailable: {str(e)}"

    time.sleep(5)

    # Political news
    print("Researching political news...")
    try:
        report['political'] = research_political_news()[:3000]
        print("Political done")
    except Exception as e:
        report['political'] = f"Political unavailable: {str(e)}"

    time.sleep(5)

    # Sports with verified data
    print("Researching sports...")
    try:
        sports_markets = report.get('sports_markets', [])
        report['sports'] = research_sports_markets(sports_markets[:4]) if sports_markets else "No sports markets."
        print("Sports done")
    except Exception as e:
        report['sports'] = f"Sports unavailable: {str(e)}"

    time.sleep(5)

    # Weather
    print("Researching weather markets...")
    try:
        weather_markets = report.get('weather_markets', [])
        report['weather'] = research_weather_for_markets(weather_markets) if weather_markets else "No weather markets."
        print("Weather done")
    except Exception as e:
        report['weather'] = f"Weather unavailable: {str(e)}"

    time.sleep(10)

    # Synthesize
    print("Synthesizing...")
    try:
        synthesis_prompt = f"""You are Bina, texting Nathaniel (18, Beverly Hills) his morning intelligence report.

STRICT RULES:
1. NEVER recommend without exact resolution date
2. NEVER recommend markets above 90% or below 10% YES
3. NEVER recommend "before GTA VI" floor contracts — they lock capital for near-zero return
4. NEVER quote prices not in the verified data below — say "price unavailable" if missing
5. For sports calls, reference the real-world verified data — never assume
6. Complete all arb math when data is present — don't say "need more data"
7. When Kalshi and Polymarket disagree by >5% on the same event, flag it — that's a real edge
8. When Metaculus experts disagree with Polymarket money, flag it — expert consensus vs dumb money is real alpha
9. Connect political/economic news to specific markets — name the market and the mispricing
10. Under 400 words total

DATA SOURCES:

POLYMARKET (with odds + contract details):
{report['polymarket'][:1500]}

KALSHI MARKETS:
{report['kalshi'][:800]}

POLYMARKET vs KALSHI PRICE GAPS:
{report['cross_reference'][:600]}

METACULUS EXPERT FORECASTS:
{report['metaculus'][:600]}

EXPERT vs MARKET DISAGREEMENTS:
{report['metaculus_vs_poly'][:600]}

FRED ECONOMIC DATA (Federal Reserve):
{report['fred'][:400]}

CRYPTO:
{report['crypto'][:400]}

COMMODITIES:
{report['commodities'][:200]}

POLITICAL NEWS:
{report['political'][:1000]}

SPORTS (verified):
{report['sports'][:600]}

WEATHER MARKETS:
{report['weather'][:400]}

Write like a smart friend texting. **Bold** key numbers. Get straight to it — no greeting.

## What I found overnight
Single most actionable genuine opportunity.

## Top plays
For each: exact market, current odds, resolution date, position, confidence, why price is wrong using actual data.

## Cross-platform edge (if any)
If Polymarket and Kalshi disagree, or experts and market disagree, flag the specific gap and the play.

## Watch today
2-3 specific events next 24h. For each, name the specific market it affects and how.

## The edge
One non-obvious contrarian insight backed by verified numbers."""

        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1200,
            messages=[{"role": "user", "content": synthesis_prompt}]
        )
        report['synthesis'] = response.content[0].text
        save_memory(f"Intelligence synthesis: {report['synthesis'][:500]}", memory_type='research')
        print("Synthesis done")
    except Exception as e:
        report['synthesis'] = f"Synthesis error: {str(e)}"

    # Save
    try:
        save_keys = ['date', 'time', 'polymarket', 'kalshi', 'cross_reference',
                     'metaculus', 'metaculus_vs_poly', 'crypto', 'fred',
                     'commodities', 'political', 'sports', 'weather', 'synthesis']
        save_data = {k: report.get(k, '') for k in save_keys}
        with open(OVERNIGHT_REPORT_FILE, 'w') as f:
            json.dump(save_data, f)
        print("Report saved")
    except Exception as e:
        print(f"Save error: {str(e)}")

    print("Research complete — delivering")
    deliver_report(report)
    return report


def deliver_report(report=None):
    if report is None:
        if os.path.exists(OVERNIGHT_REPORT_FILE):
            try:
                with open(OVERNIGHT_REPORT_FILE, 'r') as f:
                    report = json.load(f)
            except:
                return
        else:
            return

    date = report.get('date', datetime.datetime.now().strftime('%Y-%m-%d'))

    if report.get('synthesis'):
        add_notification({
            'id': f'intel-{date}-{int(time.time())}',
            'type': 'intelligence',
            'subject': f'🧠 Intelligence Report — {date}',
            'from': 'Bina Research Engine',
            'body': report['synthesis'],
            'draft_reply': '',
            'read': False,
            'timestamp': time.time()
        })

    if report.get('cross_reference') and 'No significant' not in report['cross_reference']:
        add_notification({
            'id': f'xref-{date}-{int(time.time())}',
            'type': 'intelligence',
            'subject': '⚡ Cross-Platform Price Gaps',
            'from': 'Bina Arbitrage',
            'body': report['cross_reference'] + '\n\n' + report.get('metaculus_vs_poly', ''),
            'draft_reply': '',
            'read': False,
            'timestamp': time.time()
        })

    if report.get('crypto') and 'unavailable' not in report['crypto'].lower():
        add_notification({
            'id': f'crypto-{date}-{int(time.time())}',
            'type': 'intelligence',
            'subject': '📊 Crypto + Economic Data',
            'from': 'Bina Markets',
            'body': report['crypto'] + '\n\n' + report.get('fred', ''),
            'draft_reply': '',
            'read': False,
            'timestamp': time.time()
        })

    if report.get('polymarket') and 'unavailable' not in report['polymarket'].lower():
        add_notification({
            'id': f'poly-{date}-{int(time.time())}',
            'type': 'intelligence',
            'subject': '🎯 All Markets — Polymarket + Kalshi',
            'from': 'Bina Markets',
            'body': report['polymarket'][:1500] + '\n\n' + report.get('kalshi', '')[:800],
            'draft_reply': '',
            'read': False,
            'timestamp': time.time()
        })

    send_push(
        '🧠 Bina — Intel Ready',
        'Overnight research done. Check Intel Feed.',
        BINA_URL + '?open=feed',
        notif_type='feed'
    )
    print("Report delivered")


# ── Google Auth ───────────────────────────────────────────────────────────────

def get_access_token():
    response = requests.post('https://oauth2.googleapis.com/token', data={
        'refresh_token': os.environ.get('GOOGLE_REFRESH_TOKEN'),
        'client_id': os.environ.get('GOOGLE_CLIENT_ID'),
        'client_secret': os.environ.get('GOOGLE_CLIENT_SECRET'),
        'grant_type': 'refresh_token'
    })
    data = response.json()
    if 'error' in data:
        raise Exception(f"Token refresh failed: {data}")
    return data['access_token']


# ── Gmail ─────────────────────────────────────────────────────────────────────

def send_email(to, subject, body):
    try:
        access_token = get_access_token()
        message = MIMEText(body)
        message['to'] = to
        message['subject'] = subject
        message['from'] = os.environ.get('GMAIL_ADDRESS')
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        response = requests.post(
            'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
            headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'},
            json={'raw': raw}
        )
        if response.status_code == 200:
            return True, None
        return False, str(response.json())
    except Exception as e:
        return False, str(e)

def get_inbox_emails(max_results=10):
    try:
        access_token = get_access_token()
        response = requests.get(
            'https://gmail.googleapis.com/gmail/v1/users/me/messages',
            headers={'Authorization': f'Bearer {access_token}'},
            params={'maxResults': max_results, 'labelIds': 'INBOX', 'q': 'is:unread'}
        )
        emails = []
        for msg in response.json().get('messages', []):
            msg_response = requests.get(
                f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg["id"]}',
                headers={'Authorization': f'Bearer {access_token}'},
                params={'format': 'full'}
            )
            msg_data = msg_response.json()
            headers = {h['name']: h['value'] for h in msg_data.get('payload', {}).get('headers', [])}
            body = ''
            payload = msg_data.get('payload', {})
            if payload.get('body', {}).get('data'):
                body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='ignore')
            elif payload.get('parts'):
                for part in payload['parts']:
                    if part.get('mimeType') == 'text/plain' and part.get('body', {}).get('data'):
                        body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='ignore')
                        break
            emails.append({
                'id': msg['id'],
                'from': headers.get('From', 'Unknown'),
                'subject': headers.get('Subject', '(no subject)'),
                'date': headers.get('Date', ''),
                'body': body[:2000]
            })
        return emails
    except Exception as e:
        print(f"Inbox error: {str(e)}")
        return []

def draft_reply(email):
    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=500,
            system="You are Bina, drafting a reply on behalf of Nathaniel Werner, 18-year-old entrepreneur in Beverly Hills. Concise, professional. Reply body only, sign off as Nathaniel Werner.",
            messages=[{"role": "user", "content": f"Draft reply:\nFrom: {email['from']}\nSubject: {email['subject']}\n\n{email['body']}"}]
        )
        return response.content[0].text
    except Exception as e:
        return f"Could not draft: {str(e)}"


# ── Google Calendar ───────────────────────────────────────────────────────────

def get_upcoming_events(max_results=10):
    try:
        access_token = get_access_token()
        now = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        response = requests.get(
            'https://www.googleapis.com/calendar/v3/calendars/primary/events',
            headers={'Authorization': f'Bearer {access_token}'},
            params={'timeMin': now, 'maxResults': max_results, 'singleEvents': True, 'orderBy': 'startTime'}
        )
        events = []
        for item in response.json().get('items', []):
            start = item['start'].get('dateTime', item['start'].get('date', ''))
            end = item['end'].get('dateTime', item['end'].get('date', ''))
            events.append({'id': item['id'], 'title': item.get('summary', '(no title)'), 'start': start, 'end': end})
        return events
    except:
        return []

def create_calendar_event(title, start, end, description=''):
    try:
        access_token = get_access_token()
        event = {
            'summary': title, 'description': description,
            'start': {'dateTime': start, 'timeZone': 'America/Los_Angeles'},
            'end': {'dateTime': end, 'timeZone': 'America/Los_Angeles'}
        }
        response = requests.post(
            'https://www.googleapis.com/calendar/v3/calendars/primary/events',
            headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'},
            json=event
        )
        if response.status_code in [200, 201]:
            return True, response.json().get('htmlLink', '')
        return False, str(response.json())
    except Exception as e:
        return False, str(e)

def process_calendar_commands(text):
    pattern = r'CREATE_EVENT\|([^\|]+)\|([^\|]+)\|([^\|]+)\|?([^E]*)END_EVENT'
    results = []
    for match in re.findall(pattern, text, re.DOTALL):
        title, start, end = match[0].strip(), match[1].strip(), match[2].strip()
        description = match[3].strip() if match[3] else ''
        success, link = create_calendar_event(title, start, end, description)
        results.append({'title': title, 'success': success, 'link': link})
    return results


# ── Master Monitor ────────────────────────────────────────────────────────────

def master_monitor():
    print("Master monitor started")
    last_briefing_day = -1
    last_overnight_day = -1

    while True:
        try:
            la_time = datetime.datetime.utcnow() + datetime.timedelta(hours=-7)
            la_hour = la_time.hour
            la_day = la_time.timetuple().tm_yday

            if la_hour == 0 and la_day != last_overnight_day:
                last_overnight_day = la_day
                print("Midnight — launching overnight research")
                threading.Thread(target=run_overnight_research, daemon=True).start()

            if la_hour == 7 and la_day != last_briefing_day:
                last_briefing_day = la_day
                try:
                    events = get_upcoming_events(max_results=5)
                    emails = get_inbox_emails(max_results=3)
                    events_text = "\n".join([f"- {e['title']} at {e['start']}" for e in events]) or "Nothing scheduled"
                    emails_text = "\n".join([f"- From {e['from']}: {e['subject']}" for e in emails]) or "No unread emails"
                    response = client.messages.create(
                        model="claude-sonnet-4-5",
                        max_tokens=300,
                        system="You are Bina texting Nathaniel his morning briefing. Short, punchy, actionable. **Bold** important things.",
                        messages=[{"role": "user", "content": f"Morning briefing.\nSchedule:\n{events_text}\nEmails:\n{emails_text}"}]
                    )
                    add_notification({
                        'id': f'briefing-{la_day}',
                        'type': 'briefing',
                        'subject': '☀️ Morning Briefing',
                        'from': 'Bina',
                        'body': response.content[0].text,
                        'draft_reply': '',
                        'read': False,
                        'timestamp': time.time()
                    })
                    send_push('☀️ Bina', 'Morning briefing ready.', BINA_URL + '?open=feed', notif_type='feed')
                except Exception as e:
                    print(f"Morning briefing error: {str(e)}")

            seen = load_seen_emails()
            emails = get_inbox_emails(max_results=10)
            for email in emails:
                if email['id'] not in seen:
                    seen.add(email['id'])
                    if is_important_email(email):
                        draft = draft_reply(email)
                        sender = email['from'].split('<')[0].strip()
                        add_notification({
                            'id': email['id'],
                            'type': 'email',
                            'from': email['from'],
                            'subject': email['subject'],
                            'body': email['body'][:500],
                            'draft_reply': draft,
                            'read': False,
                            'timestamp': time.time()
                        })
                        save_memory(f"Email from {email['from']}: {email['subject']}", memory_type='email')
                        send_push('Bina — New Email', f'From {sender}: {email["subject"]}', BINA_URL + '?open=inbox', notif_type='email')
                    else:
                        print(f"Filtered: {email['from']}")
            save_seen_emails(seen)

        except Exception as e:
            print(f"Monitor error: {str(e)}")
        time.sleep(60)


# ── Email Processing ──────────────────────────────────────────────────────────

def process_email_commands(text):
    pattern = r'SEND_EMAIL\|(.*?)\|(.*?)\|(.*?)END_EMAIL'
    results = []
    for to, subject, body in re.findall(pattern, text, re.DOTALL):
        success, error = send_email(to.strip(), subject.strip(), body.strip())
        results.append({'to': to.strip(), 'subject': subject.strip(), 'success': success, 'error': error})
    return results

def clean_response(text):
    cleaned = re.sub(r'SEND_EMAIL\|.*?END_EMAIL', '', text, flags=re.DOTALL)
    cleaned = re.sub(r'CREATE_EVENT\|.*?END_EVENT', '', cleaned, flags=re.DOTALL)
    return cleaned.strip()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/manifest.json')
def manifest():
    return app.send_static_file('manifest.json')

@app.route('/sw.js')
def service_worker():
    return app.send_static_file('sw.js')

@app.route('/clear-subs')
def clear_subs():
    save_subscriptions([])
    return '<h2 style="color:green;font-family:monospace;padding:40px">✅ Cleared!</h2>'

@app.route('/subscribe', methods=['POST'])
def subscribe():
    sub = request.json
    subs = load_subscriptions()
    if sub not in subs:
        subs.append(sub)
        save_subscriptions(subs)
    print(f"Subscription saved. Total: {len(subs)}")
    return jsonify({'success': True})

@app.route('/vapid-public-key')
def vapid_key():
    return jsonify({'key': VAPID_PUBLIC_KEY})

@app.route('/notifications', methods=['GET'])
def get_notifications():
    notifications = load_notifications()
    unread = [n for n in notifications if not n.get('read')]
    return jsonify({'notifications': notifications, 'unread_count': len(unread)})

@app.route('/notifications/read/<notif_id>', methods=['POST'])
def mark_read(notif_id):
    notifications = load_notifications()
    for n in notifications:
        if n['id'] == notif_id:
            n['read'] = True
    save_notifications(notifications)
    return jsonify({'success': True})

@app.route('/send-draft', methods=['POST'])
def send_draft():
    data = request.json
    to, subject, body = data.get('to'), data.get('subject'), data.get('body')
    success, error = send_email(to, f"Re: {subject}", body)
    if success:
        notifications = load_notifications()
        for n in notifications:
            if n.get('subject') == subject:
                n['replied'] = True
        save_notifications(notifications)
        send_push('Bina ✅', 'Reply sent.', BINA_URL + '?open=inbox', notif_type='email')
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': error})

@app.route('/calendar', methods=['GET'])
def get_calendar():
    return jsonify({'events': get_upcoming_events(max_results=10)})

@app.route('/calendar/create', methods=['POST'])
def create_event_route():
    data = request.json
    success, link = create_calendar_event(data.get('title'), data.get('start'), data.get('end'), data.get('description', ''))
    return jsonify({'success': success, 'link': link})

@app.route('/test-push')
def test_push():
    send_push('Bina 🔔', 'Test.', BINA_URL + '?open=feed', notif_type='feed')
    return '<h2 style="color:green;font-family:monospace;padding:40px">✅ Push sent!</h2>'

@app.route('/test-research')
def test_research():
    threading.Thread(target=run_overnight_research, daemon=True).start()
    return '<h2 style="color:green;font-family:monospace;padding:40px">✅ Research started! Check Intel Feed in ~10 minutes.</h2>'

@app.route('/deliver-report')
def deliver_report_route():
    deliver_report()
    return '<h2 style="color:green;font-family:monospace;padding:40px">✅ Report delivered!</h2>'

@app.route('/test-email')
def test_email():
    success, error = send_email('iirawgunzsii@gmail.com', 'Test from Bina', 'Hey! Bina testing.')
    if success:
        return '<h2 style="color:green;font-family:monospace;padding:40px">✅ Email sent!</h2>'
    return f'<h2 style="color:red;font-family:monospace;padding:40px">❌ Failed: {error}</h2>'

@app.route('/crypto')
def get_crypto_route():
    return jsonify({'crypto': get_crypto_data(), 'fear_greed': get_fear_greed_index()})

@app.route('/polymarket')
def get_polymarket_route():
    markets = get_polymarket_markets_with_odds(limit=50)
    categorized = categorize_polymarket(markets)
    return jsonify({'count': len(markets), 'formatted': format_markets_for_analysis(categorized)})

@app.route('/kalshi')
def get_kalshi_route():
    markets = get_kalshi_markets(limit=50)
    return jsonify({'count': len(markets), 'formatted': format_kalshi_markets(markets)})

@app.route('/metaculus')
def get_metaculus_route():
    questions = get_metaculus_questions(limit=20)
    return jsonify({'count': len(questions), 'formatted': format_metaculus(questions)})

@app.route('/fred')
def get_fred_route():
    return jsonify({'data': get_fred_data()})

@app.route('/memories', methods=['GET'])
def get_memories_route():
    query = request.args.get('q', 'Nathaniel')
    memories = search_memories(query, top_k=10, threshold=0.3)
    return jsonify({'memories': memories, 'count': len(memories)})

@app.route('/generate-vapid')
def generate_vapid():
    try:
        private_key = ec.generate_private_key(ec.SECP256R1())
        pub_bytes = private_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        priv_raw = private_key.private_numbers().private_value.to_bytes(32, 'big')
        pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
        priv_b64 = base64.urlsafe_b64encode(priv_raw).rstrip(b"=").decode()
        return f"""<html><body style="font-family:monospace;padding:40px;background:#000;color:#0f0;">
        <h2>VAPID Keys</h2>
        <p><b>PUBLIC:</b></p><textarea onclick="this.select()" style="width:100%;height:60px;background:#111;color:#0f0;padding:8px;">{pub_b64}</textarea>
        <p><b>PRIVATE:</b></p><textarea onclick="this.select()" style="width:100%;height:60px;background:#111;color:#0f0;padding:8px;">{priv_b64}</textarea>
        </body></html>"""
    except Exception as e:
        import traceback
        return f'<pre style="color:red;padding:40px">{traceback.format_exc()}</pre>'

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    user_message = data.get('message', '')
    conversation_history = data.get('history', [])

    la_time = datetime.datetime.utcnow() + datetime.timedelta(hours=-7)
    la_time_str = la_time.strftime('%A, %B %d, %Y %I:%M %p')
    user_message_with_context = f"[LA Time: {la_time_str}]\n\n{user_message}"

    all_memories = get_all_context_memories(user_message)
    memory_context = format_memories(all_memories)
    msg_lower = user_message.lower()

    if any(word in msg_lower for word in ['crypto', 'bitcoin', 'btc', 'ethereum', 'eth', 'solana', 'sol', 'price', 'coin']):
        crypto_data = get_crypto_data()
        fear_greed = get_fear_greed_index()
        user_message_with_context += f"\n\nLive crypto:\n{format_crypto_report(crypto_data, fear_greed)}"

    if any(word in msg_lower for word in ['polymarket', 'prediction market', 'odds', 'bet']):
        markets = get_polymarket_markets_with_odds(limit=50)
        categorized = categorize_polymarket(markets)
        user_message_with_context += f"\n\nLive Polymarket:\n{format_markets_for_analysis(categorized)}"

    if any(word in msg_lower for word in ['kalshi']):
        kalshi = get_kalshi_markets(limit=30)
        user_message_with_context += f"\n\nLive Kalshi:\n{format_kalshi_markets(kalshi)}"

    if any(word in msg_lower for word in ['gold', 'oil', 'commodity', 'commodities']):
        user_message_with_context += f"\n\nCommodities:\n{get_real_commodity_prices()}"

    if any(word in msg_lower for word in ['fed', 'federal reserve', 'inflation', 'unemployment', 'economic data']):
        user_message_with_context += f"\n\nFRED Economic Data:\n{get_fred_data()}"

    search_triggers = ['search', 'look up', 'find', 'what is', 'who is', 'latest', 'news',
                       'current', 'stock', 'weather', 'research', 'tell me about', 'what happened',
                       'today', 'trending', 'recent', 'update', 'best', 'top', 'political', 'politics']
    deep_triggers = ['research', 'deep dive', 'everything about', 'full report', 'analyze',
                     'investigate', 'background on', 'tell me about']

    if any(word in msg_lower for word in deep_triggers):
        user_message_with_context += f"\n\nDeep research:\n{web_search(user_message, num_results=5)}"
    elif any(word in msg_lower for word in search_triggers):
        user_message_with_context += f"\n\nSearch:\n{web_search(user_message)}"

    if any(word in msg_lower for word in ['schedule', 'calendar', 'meeting', 'tomorrow', 'what do i have']):
        events = get_upcoming_events(max_results=5)
        if events:
            user_message_with_context += f"\n\nCalendar:\n" + "\n".join([f"- {e['title']} at {e['start']}" for e in events])
        else:
            user_message_with_context += "\n\nCalendar is empty."

    messages = conversation_history + [{"role": "user", "content": user_message_with_context}]

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        system=SYSTEM_PROMPT + memory_context,
        messages=messages
    )

    assistant_message = response.content[0].text
    email_results = process_email_commands(assistant_message)
    calendar_results = process_calendar_commands(assistant_message)
    display_message = clean_response(assistant_message)

    save_memory(f"Nathaniel: {user_message} | Bina: {display_message[:300]}", memory_type='conversation')

    if any(word in msg_lower for word in ['remember', 'save', 'note', 'important', "don't forget", 'remind me']):
        save_memory(f"IMPORTANT — Nathaniel said: {user_message}", memory_type='explicit')

    if any(word in msg_lower for word in ['my mom', 'my dad', 'my friend', 'my brother', 'my sister',
                                           'my girlfriend', 'i am', "i'm", 'i have', 'i work',
                                           'i live', 'i want', 'i hate', 'i love', 'my goal']):
        save_memory(f"Personal — Nathaniel: {user_message}", memory_type='personal')

    result = {'response': display_message}
    if email_results:
        sent = [e for e in email_results if e['success']]
        failed = [e for e in email_results if not e['success']]
        if sent:
            result['email_sent'] = f"✅ Email sent to {sent[0]['to']}"
            send_push('Bina ✅', f'Sent to {sent[0]["to"]}', BINA_URL + '?open=inbox', notif_type='email')
            save_memory(f"Sent email to {sent[0]['to']} — {sent[0]['subject']}", memory_type='email')
        if failed:
            result['email_error'] = f"❌ Email failed: {failed[0]['error']}"
    if calendar_results:
        created = [e for e in calendar_results if e['success']]
        if created:
            result['event_created'] = f"📅 Event created: {created[0]['title']}"
            send_push('Bina 📅', f'"{created[0]["title"]}" added', BINA_URL + '?open=feed', notif_type='feed')
            save_memory(f"Created event: {created[0]['title']}", memory_type='calendar')

    return jsonify(result)


# ── OAuth ─────────────────────────────────────────────────────────────────────

@app.route('/authorize')
def authorize():
    params = {
        'client_id': os.environ.get('GOOGLE_CLIENT_ID'),
        'redirect_uri': REDIRECT_URI,
        'response_type': 'code',
        'scope': GMAIL_SCOPES,
        'access_type': 'offline',
        'prompt': 'consent'
    }
    return redirect('https://accounts.google.com/o/oauth2/auth?' + urlencode(params))

@app.route('/oauth/callback')
def oauth_callback():
    code = request.args.get('code')
    error = request.args.get('error')
    if error:
        return f'<h2 style="color:red">Error: {error}</h2>', 400
    if not code:
        return '<h2 style="color:red">No code</h2>', 400
    token_response = requests.post('https://oauth2.googleapis.com/token', data={
        'code': code,
        'client_id': os.environ.get('GOOGLE_CLIENT_ID'),
        'client_secret': os.environ.get('GOOGLE_CLIENT_SECRET'),
        'redirect_uri': REDIRECT_URI,
        'grant_type': 'authorization_code'
    })
    tokens = token_response.json()
    refresh_token = tokens.get('refresh_token', '')
    note = "✅ Copy to GOOGLE_REFRESH_TOKEN in Railway." if refresh_token else "⚠️ No refresh token."
    return f"""<html><body style="font-family:monospace;padding:40px;background:#000;color:#0f0;">
    <h2>OAuth</h2>
    <textarea style="width:100%;height:80px;background:#111;color:#0f0;padding:8px;">{refresh_token}</textarea>
    <p>{note}</p></body></html>"""


# ── Start ─────────────────────────────────────────────────────────────────────

monitor_thread = threading.Thread(target=master_monitor, daemon=True)
monitor_thread.start()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
