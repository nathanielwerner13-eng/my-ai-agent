import os
import json
import time
import datetime
import threading
import requests
import uuid
import re
from anthropic import Anthropic
from pinecone import Pinecone

client = Anthropic()
pc = Pinecone(api_key=os.environ.get('PINECONE_API_KEY', ''))
PINECONE_INDEX = os.environ.get('PINECONE_INDEX', 'bina-memory')

BINA_URL = os.environ.get('BINA_URL', 'https://my-ai-agent-production-5e17.up.railway.app')
SERPER_API_KEY = os.environ.get('SERPER_API_KEY', '')
NEWS_API_KEY = os.environ.get('NEWS_API_KEY', '')
RAPIDAPI_KEY = os.environ.get('RAPIDAPI_KEY', '')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
FRED_API_KEY = os.environ.get('FRED_API_KEY', '')
ALPHA_VANTAGE_KEY = os.environ.get('ALPHA_VANTAGE_KEY', '')

SEEN_NEWS_FILE = '/tmp/seen_news.json'
MARKET_HISTORY_FILE = '/tmp/market_history.json'

print("Bina Research Worker starting...")


# ── Memory ────────────────────────────────────────────────────────────────────

def get_embedding(text):
    try:
        response = requests.post(
            'https://api.openai.com/v1/embeddings',
            headers={'Authorization': f'Bearer {OPENAI_API_KEY}',
                     'Content-Type': 'application/json'},
            json={'model': 'text-embedding-ada-002', 'input': text[:8000]}
        )
        if response.status_code == 200:
            return response.json()['data'][0]['embedding']
        return None
    except:
        return None

def save_memory(text, memory_type='research'):
    try:
        embedding = get_embedding(text)
        if not embedding:
            return
        index = pc.Index(PINECONE_INDEX)
        index.upsert(vectors=[{
            'id': str(uuid.uuid4()),
            'values': embedding,
            'metadata': {
                'text': text[:1000],
                'type': memory_type,
                'timestamp': time.time(),
                'date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
            }
        }])
    except Exception as e:
        print(f"Memory error: {str(e)}")


# ── Push ──────────────────────────────────────────────────────────────────────

def push_to_main_app(subject, body, notif_type='intelligence'):
    try:
        response = requests.post(
            f'{BINA_URL}/internal/add-notification',
            json={
                'id': f'research-{int(time.time())}-{uuid.uuid4().hex[:6]}',
                'type': notif_type,
                'subject': subject,
                'from': 'Bina Research Worker',
                'body': body,
                'draft_reply': '',
                'read': False,
                'timestamp': time.time()
            },
            timeout=15
        )
        if response.status_code == 200:
            print(f"✅ Pushed: {subject[:60]}")
        else:
            print(f"❌ Push failed {response.status_code}")
    except Exception as e:
        print(f"Push error: {str(e)}")


# ── Web Search ────────────────────────────────────────────────────────────────

def web_search(query, num_results=5):
    if not SERPER_API_KEY:
        return ""
    try:
        response = requests.post(
            'https://google.serper.dev/search',
            headers={'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'},
            json={'q': query, 'num': num_results},
            timeout=10
        )
        data = response.json()
        output = f"[{query}]\n"
        if data.get('answerBox'):
            answer = (data['answerBox'].get('answer') or
                     data['answerBox'].get('snippet') or '')
            if answer:
                output += f"DIRECT: {answer}\n"
        for r in data.get('organic', [])[:num_results]:
            output += f"• {r.get('title', '')}: {r.get('snippet', '')}\n"
        for n in data.get('news', [])[:4]:
            output += f"• NEWS ({n.get('date', 'recent')}): {n.get('title', '')} — {n.get('snippet', '')}\n"
        return output
    except Exception as e:
        return f"Search error: {str(e)}"


# ── NewsAPI ───────────────────────────────────────────────────────────────────

def get_news(category='general', query=None, hours=6):
    if not NEWS_API_KEY:
        return []
    try:
        if query:
            from_time = (datetime.datetime.utcnow() -
                        datetime.timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
            response = requests.get(
                'https://newsapi.org/v2/everything',
                params={
                    'apiKey': NEWS_API_KEY,
                    'q': query,
                    'language': 'en',
                    'sortBy': 'publishedAt',
                    'pageSize': 15,
                    'from': from_time
                },
                timeout=10
            )
        else:
            response = requests.get(
                'https://newsapi.org/v2/top-headlines',
                params={
                    'apiKey': NEWS_API_KEY,
                    'language': 'en',
                    'pageSize': 20,
                    'category': category
                },
                timeout=10
            )
        if response.status_code == 200:
            return response.json().get('articles', [])
        return []
    except Exception as e:
        print(f"NewsAPI error: {str(e)}")
        return []

def format_articles(articles, max=15):
    output = ""
    for a in articles[:max]:
        title = a.get('title', '')
        desc = a.get('description', '') or ''
        source = a.get('source', {}).get('name', '?')
        published = a.get('publishedAt', '')[:16]
        if title and '[Removed]' not in title:
            output += f"• [{source}] {title}"
            if desc:
                output += f" — {desc[:120]}"
            output += f" ({published})\n"
    return output


# ── Polymarket — FULL scan, wider filter ─────────────────────────────────────

def get_all_polymarket_markets(limit=200):
    """Get ALL markets — wider filter to find more opportunities."""
    try:
        response = requests.get(
            'https://gamma-api.polymarket.com/markets',
            params={'limit': limit, 'active': 'true', 'closed': 'false'},
            timeout=20
        )
        if response.status_code != 200:
            return []

        markets = response.json()
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        processed = []

        for m in markets:
            title = str(m.get('question', m.get('title', '')))
            title_lower = title.lower()

            # Only skip GTA VI floor contracts
            if any(skip in title_lower for skip in
                   ['gta vi', 'gta6', 'grand theft auto']):
                continue

            volume = m.get('volume', 0) or 0
            try:
                volume = float(str(volume).replace(',', ''))
            except:
                volume = 0

            # Get odds
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

            # Resolution date
            end_date = m.get('endDate', '')
            days_until = None
            end_str = 'Unknown'
            if end_date:
                try:
                    end_dt = datetime.datetime.fromisoformat(
                        end_date.replace('Z', '+00:00'))
                    days_until = (end_dt - now_utc).days
                    if days_until < 0:
                        continue
                    end_str = end_dt.strftime('%b %d, %Y')
                except:
                    pass

            description = (m.get('description', '') or '')[:500]
            has_floor = any(p in description.lower() for p in
                          ['50-50', '50/50', 'neither', 'resolve 0.5', '0.50'])

            # WIDER filter — only skip if extremely certain (>95% or <5%)
            if yes_price and (yes_price > 0.95 or yes_price < 0.05):
                continue

            processed.append({
                'title': title,
                'yes_price': yes_price,
                'no_price': no_price,
                'volume': volume,
                'end_date': end_str,
                'days_until': days_until,
                'has_floor': has_floor,
                'description': description,
                'tags': [t.get('label', '') if isinstance(t, dict) else str(t)
                         for t in (m.get('tags') or [])]
            })

        processed.sort(key=lambda x: x['volume'], reverse=True)
        print(f"Polymarket: {len(processed)} valid markets found")
        return processed

    except Exception as e:
        print(f"Polymarket error: {str(e)}")
        return []


# ── Market History ────────────────────────────────────────────────────────────

def load_market_history():
    try:
        if os.path.exists(MARKET_HISTORY_FILE):
            with open(MARKET_HISTORY_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    return {}

def save_market_history(markets):
    try:
        history = {}
        for m in markets:
            if m.get('yes_price'):
                history[m['title']] = {
                    'yes': m['yes_price'],
                    'time': time.time()
                }
        with open(MARKET_HISTORY_FILE, 'w') as f:
            json.dump(history, f)
    except:
        pass

def detect_odds_movement(markets, history):
    movements = []
    for m in markets:
        title = m['title']
        yes = m.get('yes_price')
        if not yes or title not in history:
            continue
        old_yes = history[title].get('yes', yes)
        diff = yes - old_yes
        if abs(diff) > 0.03:
            movements.append({
                'title': title,
                'old_yes': old_yes,
                'new_yes': yes,
                'diff': diff,
                'volume': m.get('volume', 0),
                'end_date': m.get('end_date', '?'),
                'days_until': m.get('days_until')
            })
    movements.sort(key=lambda x: abs(x['diff']), reverse=True)
    return movements


# ── FRED ──────────────────────────────────────────────────────────────────────

def get_fred_snapshot():
    if not FRED_API_KEY:
        return ""
    indicators = {
        'FEDFUNDS': 'Fed Funds Rate',
        'UNRATE': 'Unemployment',
        'DGS10': '10yr Treasury',
        'DCOILWTICO': 'WTI Oil',
        'GOLDAMGBD228NLBM': 'Gold'
    }
    output = ""
    for series_id, name in indicators.items():
        try:
            response = requests.get(
                'https://api.stlouisfed.org/fred/series/observations',
                params={
                    'series_id': series_id,
                    'api_key': FRED_API_KEY,
                    'file_type': 'json',
                    'limit': 2,
                    'sort_order': 'desc'
                },
                timeout=8
            )
            if response.status_code == 200:
                obs = response.json().get('observations', [])
                if obs and obs[0].get('value') != '.':
                    current = float(obs[0]['value'])
                    prev = float(obs[1]['value']) if (
                        len(obs) > 1 and obs[1].get('value') != '.') else current
                    change = current - prev
                    arrow = '📈' if change > 0 else '📉' if change < 0 else '➡️'
                    output += f"{arrow} {name}: {current} (Δ{change:+.2f})\n"
            time.sleep(0.3)
        except:
            pass
    return output


# ── Seen News ─────────────────────────────────────────────────────────────────

def load_seen_news():
    try:
        if os.path.exists(SEEN_NEWS_FILE):
            with open(SEEN_NEWS_FILE, 'r') as f:
                return set(json.load(f).get('titles', [])[-1000:])
    except:
        pass
    return set()

def save_seen_news(seen):
    try:
        with open(SEEN_NEWS_FILE, 'w') as f:
            json.dump({'titles': list(seen)[-1000:]}, f)
    except:
        pass


# ── NBA Data ──────────────────────────────────────────────────────────────────

def get_nba_data():
    output = ""
    if RAPIDAPI_KEY:
        try:
            response = requests.get(
                'https://nba-api-free-data.p.rapidapi.com/nba-live-score',
                headers={
                    'x-rapidapi-host': 'nba-api-free-data.p.rapidapi.com',
                    'x-rapidapi-key': RAPIDAPI_KEY
                },
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                games = (data.get('events') or data.get('games') or
                        data.get('scoreboard', {}).get('games') or [])
                if games:
                    output += f"NBA Live ({len(games)} games):\n"
                    for g in games[:6]:
                        try:
                            home = g.get('homeTeam', g.get('home', {}))
                            away = g.get('awayTeam', g.get('away', {}))
                            home_name = (home.get('teamName') or
                                       home.get('teamTricode') or 'Home')
                            away_name = (away.get('teamName') or
                                       away.get('teamTricode') or 'Away')
                            home_score = home.get('score', '?')
                            away_score = away.get('score', '?')
                            status = (g.get('status', {}).get('type', {})
                                     .get('description') or
                                     g.get('statusText') or 'Unknown')
                            output += f"  {away_name} {away_score} @ {home_name} {home_score} — {status}\n"
                        except:
                            pass
        except Exception as e:
            print(f"NBA API error: {str(e)}")

    # Always search for current playoff context
    searches = [
        "NBA Conference Finals 2026 series scores today results",
        "OKC Thunder Spurs Knicks Cavaliers NBA playoffs bracket 2026",
        "NBA Finals 2026 who advanced who eliminated"
    ]
    for s in searches:
        output += web_search(s, num_results=4)
        time.sleep(1)
    return output

def get_nhl_data():
    output = ""
    searches = [
        "NHL Conference Finals 2026 series scores today",
        "Colorado Avalanche Carolina Hurricanes NHL playoffs 2026 series",
        "NHL Stanley Cup Finals 2026 who advanced"
    ]
    for s in searches:
        output += web_search(s, num_results=4)
        time.sleep(1)
    return output


# ── Deep Political Research ───────────────────────────────────────────────────

def deep_political_research():
    """30+ searches — comprehensive political coverage."""
    output = ""
    topics = [
        # US Politics
        "Trump major announcement news today",
        "US Congress vote bill passed today",
        "2026 midterm election polling latest",
        "Trump approval rating latest",
        "Republican Democrat news today",
        # World Politics — high market impact
        "China Taiwan military activity news today",
        "Russia Ukraine war ceasefire peace talks today",
        "Israel Gaza war news today",
        "Iran nuclear deal talks news",
        "NATO military news today",
        "North Korea missile nuclear news",
        "Saudi Arabia OPEC oil news today",
        "India Pakistan conflict news",
        "South China Sea military news",
        "Strait of Hormuz shipping news",
        # Market-moving political
        "Polymarket prediction odds politics today",
        "election odds movement today",
        "geopolitical risk markets today",
        "political crisis breaking news today",
        "war risk escalation news today",
        # Economic/Political
        "Federal Reserve rate decision news",
        "US inflation CPI data today",
        "oil price spike geopolitical",
        "sanctions news today",
        "trade war tariffs news today",
        # Sports political context
        "FIFA World Cup 2026 team news form",
        "NBA Finals 2026 predictions betting odds",
        "NHL Stanley Cup 2026 series update",
        "sports betting market movement today",
        "World Cup favorites analysis 2026"
    ]

    for i, topic in enumerate(topics):
        result = web_search(topic, num_results=3)
        output += f"\n{result}"
        time.sleep(1.5)
        if i % 5 == 0:
            print(f"  Political research: {i+1}/{len(topics)} searches done")

    return output


# ── Breaking News Monitor ─────────────────────────────────────────────────────

def run_breaking_news_monitor():
    """Every 15 min — broad market-moving triggers."""
    print(f"Breaking news check: {datetime.datetime.now().strftime('%H:%M')}")

    triggers = [
        'strait of hormuz', 'taiwan strait', 'china military', 'invasion begins',
        'nuclear', 'ceasefire announced', 'war declared', 'troops deployed',
        'sanctions imposed', 'nato invoked', 'missile launch', 'attacked',
        'federal reserve emergency', 'rate cut surprise', 'rate hike surprise',
        'market crash', 'bank collapse', 'debt default', 'oil embargo',
        'opec emergency', 'trump impeach', 'assassination', 'coup',
        'nba finals game', 'stanley cup game', 'series clinched',
        'eliminated playoffs', 'advances to finals', 'overtime winner',
        'world cup qualifier', 'upset win', 'injury star player'
    ]

    seen_news = load_seen_news()
    alerts = []

    categories = ['general', 'politics', 'business', 'sports']
    all_articles = []
    for cat in categories:
        articles = get_news(category=cat)
        all_articles.extend(articles)
        time.sleep(0.5)

    # Extra high-value searches
    for query in ['breaking geopolitical crisis today',
                  'NBA game result tonight score',
                  'NHL playoff game result tonight']:
        articles = get_news(query=query, hours=3)
        all_articles.extend(articles)
        time.sleep(0.5)

    for article in all_articles:
        title = article.get('title', '')
        if not title or '[Removed]' in title or title in seen_news:
            continue
        combined = (title + ' ' + (article.get('description', '') or '')).lower()
        matched = [t for t in triggers if t in combined]
        if matched:
            alerts.append({
                'title': title,
                'description': article.get('description', ''),
                'source': article.get('source', {}).get('name', '?'),
                'published': article.get('publishedAt', '')[:16],
                'keywords': matched
            })
            seen_news.add(title)

    save_seen_news(seen_news)

    if alerts:
        print(f"⚡ {len(alerts)} breaking alerts!")
        body = f"**{len(alerts)} market-moving stories:**\n\n"
        for alert in alerts[:4]:
            body += f"**{alert['title']}**\n"
            body += f"{alert['source']} | {alert['published']}\n"
            if alert.get('description'):
                body += f"{alert['description'][:150]}\n"
            body += f"Triggers: {', '.join(alert['keywords'][:2])}\n\n"

        # Quick market impact research
        market_search = web_search(
            f"Polymarket odds {alerts[0]['title'][:50]}", num_results=3)
        body += f"**Market impact:**\n{market_search[:300]}"

        push_to_main_app(
            f"🚨 Breaking: {alerts[0]['title'][:55]}",
            body
        )
        save_memory(f"Breaking: {alerts[0]['title']}", memory_type='breaking')
    else:
        print("No breaking alerts")


# ── Sports Monitor ────────────────────────────────────────────────────────────

def run_sports_monitor():
    """Deep sports research every 30 min during game hours."""
    print(f"Sports monitor: {datetime.datetime.now().strftime('%H:%M')}")

    all_markets = get_all_polymarket_markets(limit=200)
    sports_kw = ['nba', 'nhl', 'finals', 'stanley cup', 'world cup', 'fifa',
                 'spurs', 'thunder', 'knicks', 'cavalier', 'avalanche',
                 'hurricane', 'france', 'spain', 'england', 'brazil',
                 'argentina', 'portugal', 'germany', 'netherlands']
    sports_markets = [m for m in all_markets if any(
        kw in m['title'].lower() for kw in sports_kw)]

    if not sports_markets:
        print("No sports markets")
        return

    nba_data = get_nba_data()
    nhl_data = get_nhl_data()

    # Arb calculations
    wc = [m for m in sports_markets if
          ('world cup' in m['title'].lower() or 'fifa' in m['title'].lower())
          and m.get('yes_price', 0) > 0.04]
    nba = [m for m in sports_markets if
           'nba finals' in m['title'].lower() and m.get('yes_price')]
    nhl_cup = [m for m in sports_markets if
               'stanley cup' in m['title'].lower() and m.get('yes_price')]

    wc_total = sum(m.get('yes_price', 0) for m in wc)
    nba_total = sum(m.get('yes_price', 0) for m in nba)
    nhl_total = sum(m.get('yes_price', 0) for m in nhl_cup)

    arb_section = ""
    if wc_total < 0.94:
        arb_section += f"⚡ **World Cup gap: {1-wc_total:.1%}**\n"
        for m in sorted(wc, key=lambda x: x.get('yes_price', 0), reverse=True)[:8]:
            arb_section += f"  {m['title'].replace('Will ', '').replace(' win the 2026 FIFA World Cup?', '')}: {m.get('yes_price', 0):.1%}\n"
        arb_section += f"  Total: {wc_total:.1%} | Gap: {1-wc_total:.1%}\n\n"

    if nba_total < 0.94:
        arb_section += f"⚡ **NBA Finals gap: {1-nba_total:.1%}**\n"
        for m in nba:
            arb_section += f"  {m['title'].replace('Will the ', '').replace(' win the 2026 NBA Finals?', '')}: {m.get('yes_price', 0):.1%}\n"
        arb_section += f"  Total: {nba_total:.1%} | Gap: {1-nba_total:.1%}\n\n"

    if nhl_total < 0.94:
        arb_section += f"⚡ **NHL Stanley Cup gap: {1-nhl_total:.1%}**\n"
        for m in nhl_cup:
            arb_section += f"  {m['title'].replace('Will the ', '').replace(' win the 2026 NHL Stanley Cup?', '')}: {m.get('yes_price', 0):.1%}\n"
        arb_section += f"  Total: {nhl_total:.1%} | Gap: {1-nhl_total:.1%}\n\n"

    prompt = f"""You are Bina's sports analyst. Be extremely specific and brief.

ACTUAL NBA DATA (search + API):
{nba_data[:1200]}

ACTUAL NHL DATA:
{nhl_data[:800]}

ACTIVE SPORTS MARKETS:
{chr(10).join([f"• {m['title']}: YES {m.get('yes_price', 0):.1%} | ${m.get('volume',0):,.0f} | {m.get('end_date','?')} ({m.get('days_until','?')}d)" for m in sports_markets[:15]])}

ARB ANALYSIS:
{arb_section}

Rules:
- Cross-reference ACTUAL game results above with market odds
- If a team just won/lost, check if the market has adjusted
- Complete arb math — if gap exists name the exact trade
- If NO edge exists, say "No sports edge right now" in one line
- MAX 200 words
- Format each pick: **Market** | Odds | Position | Why (one sentence)"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        result = response.content[0].text

        if 'no sports edge' not in result.lower():
            body = f"{result}\n\n{arb_section}"
            push_to_main_app(
                f"🏀 Sports Edge — {datetime.datetime.now().strftime('%H:%M')}",
                body
            )
        print(f"Sports done: {result[:80]}")
    except Exception as e:
        print(f"Sports error: {str(e)}")


# ── MAIN DEEP RESEARCH ────────────────────────────────────────────────────────

def run_deep_research():
    start_time = time.time()
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f"\n{'='*50}")
    print(f"DEEP RESEARCH — {now_str}")
    print(f"{'='*50}")

    data = {}

    # Phase 1 — Markets (2 min)
    print("\n[1/7] Polymarket full scan...")
    all_markets = get_all_polymarket_markets(limit=200)
    history = load_market_history()
    movements = detect_odds_movement(all_markets, history)
    save_market_history(all_markets)
    data['markets'] = all_markets
    data['movements'] = movements
    print(f"Markets: {len(all_markets)} | Movements: {len(movements)}")
    time.sleep(5)

    # Phase 2 — Deep political (8-10 min)
    print("\n[2/7] Deep political research (30 searches)...")
    data['political'] = deep_political_research()
    print("Political done")
    time.sleep(5)

    # Phase 3 — News (2 min)
    print("\n[3/7] News collection...")
    world_news = get_news(category='world') or []
    political_news = get_news(category='politics') or []
    sports_news = get_news(category='sports') or []
    business_news = get_news(category='business') or []
    all_news = world_news + political_news + sports_news + business_news
    data['news'] = format_articles(all_news, max=25)
    print(f"News: {len(all_news)} articles")
    time.sleep(5)

    # Phase 4 — Sports (4 min)
    print("\n[4/7] Sports research...")
    data['nba'] = get_nba_data()
    time.sleep(3)
    data['nhl'] = get_nhl_data()
    time.sleep(3)
    print("Sports done")
    time.sleep(5)

    # Phase 5 — FRED (1 min)
    print("\n[5/7] Economic data...")
    data['fred'] = get_fred_snapshot()
    print("FRED done")
    time.sleep(5)

    # Phase 6 — Odds movements alert (1 min)
    print("\n[6/7] Checking odds movements...")
    movements_text = ""
    if movements:
        movements_text = "**Odds moved since last check:**\n"
        for mv in movements[:5]:
            d = mv['diff']
            arrow = "⬆️" if d > 0 else "⬇️"
            movements_text += f"{arrow} **{mv['title']}**: {mv['old_yes']:.1%} → {mv['new_yes']:.1%} ({d:+.1%})\n"
            movements_text += f"  Vol: ${mv['volume']:,.0f} | Resolves: {mv['end_date']}\n"
    print(f"Movements: {len(movements)}")
    time.sleep(5)

    # Phase 7 — Synthesize (2 min)
    elapsed = time.time() - start_time
    print(f"\n[7/7] Synthesizing... ({elapsed:.0f}s collected)")

    # Arb calculations
    wc = [m for m in all_markets if
          ('world cup' in m['title'].lower() or 'fifa' in m['title'].lower())
          and m.get('yes_price', 0) > 0.04]
    nba_f = [m for m in all_markets if
             'nba finals' in m['title'].lower() and m.get('yes_price')]
    nhl_c = [m for m in all_markets if
             'stanley cup' in m['title'].lower() and m.get('yes_price')]

    wc_total = sum(m.get('yes_price', 0) for m in wc)
    nba_total = sum(m.get('yes_price', 0) for m in nba_f)
    nhl_total = sum(m.get('yes_price', 0) for m in nhl_c)

    def fmt(markets, n=20):
        out = ""
        for m in markets[:n]:
            yes = m.get('yes_price')
            vol = m.get('volume', 0)
            end = m.get('end_date', '?')
            days = m.get('days_until', '?')
            floor = " [FLOOR]" if m.get('has_floor') else ""
            if yes:
                out += f"• {m['title']}: YES {yes:.1%} | ${vol:,.0f} | {end} ({days}d){floor}\n"
        return out

    synthesis_prompt = f"""You are Bina. Nathaniel wants SHORT, ACTIONABLE picks — not analysis essays.

Research: {elapsed:.0f}s | {len(all_markets)} markets | {len(all_news)} news articles

RULES — CRITICAL:
1. Give MAX 5 picks total
2. Each pick = one line: **Market** | YES X% | Resolves date | BUY YES/NO | Confidence H/M/L | One sentence why
3. SKIP any market where you cannot explain WHY the price is wrong with SPECIFIC data
4. SKIP floor contracts, markets >95% or <5%, markets resolving in >200 days unless exceptional
5. For sports: you MUST reference actual game data below — no assumptions
6. If truly no edge exists in a category, say "No [category] edge" — don't invent picks
7. Do NOT repeat the same market twice
8. Total response under 300 words

ALL MARKETS ({len(all_markets)} total):
{fmt(all_markets, 25)}

ODDS MOVEMENTS:
{movements_text if movements_text else "No significant movements"}

WORLD CUP ARB: {len(wc)} teams total YES {wc_total:.1%} gap {1-wc_total:.1%}
{chr(10).join([f"• {m['title'].replace('Will ','').replace(' win the 2026 FIFA World Cup?','')}: {m.get('yes_price',0):.1%}" for m in sorted(wc, key=lambda x: x.get('yes_price',0), reverse=True)[:8]])}

NBA FINALS ARB: {len(nba_f)} teams total YES {nba_total:.1%} gap {1-nba_total:.1%}
{chr(10).join([f"• {m['title'].replace('Will the ','').replace(' win the 2026 NBA Finals?','')}: {m.get('yes_price',0):.1%}" for m in nba_f])}

NHL ARB: {len(nhl_c)} teams total YES {nhl_total:.1%} gap {1-nhl_total:.1%}
{chr(10).join([f"• {m['title'].replace('Will the ','').replace(' win the 2026 NHL Stanley Cup?','')}: {m.get('yes_price',0):.1%}" for m in nhl_c])}

NBA LIVE DATA:
{data.get('nba','')[:1000]}

NHL DATA:
{data.get('nhl','')[:600]}

POLITICAL RESEARCH (30 searches):
{data.get('political','')[:2500]}

NEWS ({len(all_news)} articles):
{data.get('news','')[:1200]}

FRED:
{data.get('fred','')}

FORMAT YOUR RESPONSE EXACTLY LIKE THIS:

## Picks

**[Market name]** | YES X% | Resolves [date] ([X]d) | [BUY YES / BUY NO] | [H/M/L] | [Why in one sentence using specific data]

## Odds Movements
[If any market moved >3%, flag it here with one line explanation of what it means]

## Watch
[2-3 specific events in next 6 hours that could create opportunities, one line each]"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=800,
            messages=[{"role": "user", "content": synthesis_prompt}]
        )
        synthesis = response.content[0].text
        total_time = time.time() - start_time
        print(f"\n✅ Complete in {total_time:.0f}s")

        save_memory(f"Research: {synthesis[:400]}", memory_type='research')

        # Push main report — SHORT
        push_to_main_app(
            f"🧠 Picks — {datetime.datetime.now().strftime('%H:%M')} ({total_time:.0f}s)",
            synthesis
        )

        # Push odds movements separately if significant
        if movements_text:
            push_to_main_app(
                f"⚡ Odds Moved — {datetime.datetime.now().strftime('%H:%M')}",
                movements_text
            )

        # Push arb if actionable gap >8%
        arb_body = ""
        if 1 - wc_total > 0.08 and len(wc) >= 6:
            arb_body += f"**World Cup: {1-wc_total:.1%} gap**\n"
            for m in sorted(wc, key=lambda x: x.get('yes_price', 0), reverse=True):
                arb_body += f"• {m['title'].replace('Will ','').replace(' win the 2026 FIFA World Cup?','')}: {m.get('yes_price',0):.1%}\n"
            arb_body += f"Total top teams: {wc_total:.1%}\n\n"

        if 1 - nba_total > 0.08 and len(nba_f) >= 2:
            arb_body += f"**NBA Finals: {1-nba_total:.1%} gap**\n"
            for m in nba_f:
                arb_body += f"• {m['title'].replace('Will the ','').replace(' win the 2026 NBA Finals?','')}: {m.get('yes_price',0):.1%}\n"

        if 1 - nhl_total > 0.08 and len(nhl_c) >= 2:
            arb_body += f"**NHL Cup: {1-nhl_total:.1%} gap**\n"
            for m in nhl_c:
                arb_body += f"• {m['title'].replace('Will the ','').replace(' win the 2026 NHL Stanley Cup?','')}: {m.get('yes_price',0):.1%}\n"

        if arb_body:
            push_to_main_app("⚡ Arb Gaps", arb_body)

        return synthesis

    except Exception as e:
        print(f"Synthesis error: {str(e)}")
        return None


# ── Scheduler ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("BINA RESEARCH WORKER ONLINE")
    print(f"Started: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("Schedule:")
    print("  Breaking news: every 15 min")
    print("  Sports monitor: every 30 min (4pm-1am LA)")
    print("  Deep research: every 3 hours")
    print("=" * 50)

    last_deep = 0
    last_breaking = 0
    last_sports = 0

    print("\nRunning initial deep research...")
    try:
        threading.Thread(target=run_deep_research, daemon=True).start()
    except Exception as e:
        print(f"Initial research error: {str(e)}")

    while True:
        try:
            now = time.time()
            la_time = datetime.datetime.utcnow() + datetime.timedelta(hours=-7)
            la_hour = la_time.hour

            if now - last_breaking > 900:
                last_breaking = now
                threading.Thread(
                    target=run_breaking_news_monitor, daemon=True).start()

            if now - last_sports > 1800 and (la_hour >= 16 or la_hour <= 1):
                last_sports = now
                threading.Thread(
                    target=run_sports_monitor, daemon=True).start()

            if now - last_deep > 10800:
                last_deep = now
                threading.Thread(
                    target=run_deep_research, daemon=True).start()

            time.sleep(60)

        except Exception as e:
            print(f"Scheduler error: {str(e)}")
            time.sleep(60)


if __name__ == '__main__':
    main()
