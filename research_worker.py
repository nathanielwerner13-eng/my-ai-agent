import os
import json
import time
import datetime
import threading
import requests
import uuid
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
            headers={'X-API-KEY': SERPER_API_KEY,
                     'Content-Type': 'application/json'},
            json={'q': query, 'num': num_results},
            timeout=10
        )
        data = response.json()
        output = f"[{query}]\n"
        if data.get('answerBox'):
            answer = (data['answerBox'].get('answer') or
                     data['answerBox'].get('snippet') or '')
            if answer:
                output += f"DIRECT ANSWER: {answer}\n"
        for r in data.get('organic', [])[:num_results]:
            output += f"• {r.get('title','')}: {r.get('snippet','')}\n"
        for n in data.get('news', [])[:3]:
            output += f"• NEWS ({n.get('date','recent')}): {n.get('title','')} — {n.get('snippet','')}\n"
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
                        datetime.timedelta(hours=hours)
                        ).strftime('%Y-%m-%dT%H:%M:%SZ')
            r = requests.get(
                'https://newsapi.org/v2/everything',
                params={'apiKey': NEWS_API_KEY, 'q': query,
                        'language': 'en', 'sortBy': 'publishedAt',
                        'pageSize': 15, 'from': from_time},
                timeout=10
            )
        else:
            r = requests.get(
                'https://newsapi.org/v2/top-headlines',
                params={'apiKey': NEWS_API_KEY, 'language': 'en',
                        'pageSize': 20, 'category': category},
                timeout=10
            )
        if r.status_code == 200:
            return r.json().get('articles', [])
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
        pub = a.get('publishedAt', '')[:16]
        if title and '[Removed]' not in title:
            output += f"• [{source}] {title}"
            if desc:
                output += f" — {desc[:100]}"
            output += f" ({pub})\n"
    return output


# ── Polymarket ────────────────────────────────────────────────────────────────

def get_all_polymarket_markets(limit=200):
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

            # Skip GTA VI floor contracts only
            if any(s in title_lower for s in ['gta vi', 'gta6', 'grand theft auto']):
                continue

            volume = m.get('volume', 0) or 0
            try:
                volume = float(str(volume).replace(',', ''))
            except:
                volume = 0

            yes_price = None
            no_price = None
            try:
                op = m.get('outcomePrices', '[]')
                if isinstance(op, str):
                    prices = json.loads(op)
                elif isinstance(op, list):
                    prices = op
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

            # Wide filter — skip only near-certain
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
        print(f"Polymarket: {len(processed)} markets")
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
        old = history[title].get('yes', yes)
        diff = yes - old
        if abs(diff) > 0.03:
            movements.append({
                'title': title,
                'old_yes': old,
                'new_yes': yes,
                'diff': diff,
                'volume': m.get('volume', 0),
                'end_date': m.get('end_date', '?'),
                'days_until': m.get('days_until')
            })
    movements.sort(key=lambda x: abs(x['diff']), reverse=True)
    return movements


# ── VERIFIED Sports Data ──────────────────────────────────────────────────────

def get_verified_nba_status():
    """Run multiple searches and build a verified picture of NBA status.
    Returns a fact-checked summary only — no hallucination."""
    print("  Verifying NBA status with multiple searches...")
    searches = [
        "NBA playoffs 2026 conference finals teams who is playing right now",
        "NBA Finals 2026 teams confirmed who advanced",
        "OKC Thunder NBA playoffs 2026 current status eliminated or alive",
        "San Antonio Spurs NBA playoffs 2026 current status",
        "New York Knicks NBA playoffs 2026 current status",
        "NBA bracket 2026 conference finals matchups today"
    ]
    all_results = ""
    for s in searches:
        result = web_search(s, num_results=5)
        all_results += f"\n{result}"
        time.sleep(1.5)

    # Ask Claude to extract ONLY verified facts
    try:
        extract = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=400,
            messages=[{"role": "user", "content": f"""From these search results, extract ONLY verified NBA playoff facts.
If something is not clearly confirmed in multiple sources, say "UNCONFIRMED".
Do NOT infer or assume — only state what is explicitly mentioned.

SEARCH RESULTS:
{all_results[:3000]}

Format:
- Teams confirmed in NBA Finals or Conference Finals: [list or UNKNOWN]
- Teams confirmed eliminated: [list or UNKNOWN]  
- Current series scores if mentioned: [details or UNKNOWN]
- OKC Thunder status: [alive/eliminated/UNKNOWN]
- San Antonio Spurs status: [alive/eliminated/UNKNOWN]
- New York Knicks status: [alive/eliminated/UNKNOWN]
- Any other confirmed facts: [details]"""}]
        )
        return all_results, extract.content[0].text
    except:
        return all_results, "Could not verify NBA status"

def get_verified_nhl_status():
    """Verified NHL status."""
    print("  Verifying NHL status...")
    searches = [
        "NHL playoffs 2026 conference finals teams playing now",
        "Colorado Avalanche NHL playoffs 2026 current status series",
        "Carolina Hurricanes NHL playoffs 2026 current status series",
        "NHL Stanley Cup Finals 2026 teams confirmed",
        "NHL bracket 2026 conference finals matchups"
    ]
    all_results = ""
    for s in searches:
        result = web_search(s, num_results=5)
        all_results += f"\n{result}"
        time.sleep(1.5)

    try:
        extract = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=300,
            messages=[{"role": "user", "content": f"""Extract ONLY verified NHL playoff facts. If not clearly confirmed, say UNKNOWN.

{all_results[:2000]}

Format:
- Teams in Conference Finals: [list or UNKNOWN]
- Teams eliminated: [list or UNKNOWN]
- Colorado Avalanche status: [alive/eliminated/UNKNOWN + series info]
- Carolina Hurricanes status: [alive/eliminated/UNKNOWN + series info]
- Vegas Golden Knights status: [alive/eliminated/UNKNOWN]"""}]
        )
        return all_results, extract.content[0].text
    except:
        return all_results, "Could not verify NHL status"


# ── FRED ──────────────────────────────────────────────────────────────────────

def get_fred_snapshot():
    if not FRED_API_KEY:
        return ""
    indicators = {
        'FEDFUNDS': 'Fed Rate',
        'UNRATE': 'Unemployment',
        'DGS10': '10yr Treasury',
        'DCOILWTICO': 'WTI Oil',
        'GOLDAMGBD228NLBM': 'Gold'
    }
    output = ""
    for series_id, name in indicators.items():
        try:
            r = requests.get(
                'https://api.stlouisfed.org/fred/series/observations',
                params={'series_id': series_id, 'api_key': FRED_API_KEY,
                        'file_type': 'json', 'limit': 2, 'sort_order': 'desc'},
                timeout=8
            )
            if r.status_code == 200:
                obs = r.json().get('observations', [])
                if obs and obs[0].get('value') != '.':
                    curr = float(obs[0]['value'])
                    prev = float(obs[1]['value']) if (
                        len(obs) > 1 and obs[1].get('value') != '.') else curr
                    change = curr - prev
                    arrow = '📈' if change > 0 else '📉' if change < 0 else '➡️'
                    output += f"{arrow} {name}: {curr} (Δ{change:+.2f}) {obs[0]['date']}\n"
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


# ── Deep Political Research ───────────────────────────────────────────────────

def deep_political_research():
    """30 searches — real political coverage."""
    output = ""
    topics = [
        "Trump major policy announcement today",
        "US Congress legislation vote today",
        "2026 midterm election polling",
        "China Taiwan military news today",
        "Russia Ukraine war ceasefire today",
        "Israel Iran nuclear news today",
        "Middle East conflict breaking today",
        "NATO military news today",
        "North Korea missile news",
        "Strait of Hormuz oil shipping news",
        "OPEC oil production news today",
        "Saudi Arabia geopolitical news",
        "India Pakistan tensions today",
        "South China Sea military today",
        "Federal Reserve rate decision news",
        "US inflation CPI data today",
        "oil price geopolitical risk today",
        "sanctions news today",
        "trade war tariffs news today",
        "Polymarket political odds movement today",
        "election prediction market odds today",
        "geopolitical risk market impact today",
        "political crisis breaking today",
        "war escalation news today",
        "Trump approval rating latest poll",
        "2028 presidential race news",
        "Republican Democrat news today",
        "world leaders summit meeting today",
        "nuclear deal talks news today",
        "coup assassination political news today"
    ]
    for i, topic in enumerate(topics):
        result = web_search(topic, num_results=3)
        output += f"\n{result}"
        time.sleep(1.5)
        if (i + 1) % 10 == 0:
            print(f"  Political: {i+1}/{len(topics)} done")
    return output


# ── Breaking News Monitor ─────────────────────────────────────────────────────

def run_breaking_news_monitor():
    print(f"Breaking news: {datetime.datetime.now().strftime('%H:%M')}")
    triggers = [
        'strait of hormuz', 'taiwan strait', 'china military', 'invasion',
        'nuclear', 'ceasefire', 'war declared', 'troops deployed',
        'sanctions', 'nato invoked', 'missile launch', 'attacked',
        'federal reserve emergency', 'rate cut surprise', 'rate hike surprise',
        'market crash', 'bank collapse', 'debt default', 'oil embargo',
        'opec emergency', 'trump impeach', 'assassination attempt', 'coup',
        'series clinched', 'advances to finals', 'eliminated playoffs',
        'nba finals', 'stanley cup', 'world cup result'
    ]

    seen_news = load_seen_news()
    alerts = []

    all_articles = []
    for cat in ['general', 'politics', 'business', 'sports']:
        all_articles.extend(get_news(category=cat))
        time.sleep(0.5)

    for query in ['breaking geopolitical crisis today',
                  'NBA playoffs result tonight',
                  'NHL playoffs result tonight']:
        all_articles.extend(get_news(query=query, hours=3))
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

        market_search = web_search(
            f"Polymarket odds {alerts[0]['title'][:50]}", num_results=3)
        body += f"**Market impact:**\n{market_search[:300]}"

        push_to_main_app(
            f"🚨 Breaking: {alerts[0]['title'][:55]}", body)
        save_memory(f"Breaking: {alerts[0]['title']}", memory_type='breaking')
    else:
        print("No breaking alerts")


# ── Sports Monitor ────────────────────────────────────────────────────────────

def run_sports_monitor():
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

    # Get VERIFIED status before making any calls
    nba_raw, nba_verified = get_verified_nba_status()
    nhl_raw, nhl_verified = get_verified_nhl_status()

    # Arb math
    wc = [m for m in sports_markets if
          ('world cup' in m['title'].lower() or 'fifa' in m['title'].lower())
          and m.get('yes_price', 0) > 0.04]
    nba_f = [m for m in sports_markets if
             'nba finals' in m['title'].lower() and m.get('yes_price')]
    nhl_c = [m for m in sports_markets if
             'stanley cup' in m['title'].lower() and m.get('yes_price')]

    wc_total = sum(m.get('yes_price', 0) for m in wc)
    nba_total = sum(m.get('yes_price', 0) for m in nba_f)
    nhl_total = sum(m.get('yes_price', 0) for m in nhl_c)

    prompt = f"""You are Bina's sports analyst. You have VERIFIED facts about current playoff status.

VERIFIED NBA STATUS (extracted from multiple sources):
{nba_verified}

VERIFIED NHL STATUS:
{nhl_verified}

ACTIVE MARKETS:
{chr(10).join([f"• {m['title']}: YES {m.get('yes_price',0):.1%} | ${m.get('volume',0):,.0f} | {m.get('end_date','?')} ({m.get('days_until','?')}d)" for m in sports_markets[:15]])}

NBA Finals market totals: {nba_total:.1%} (gap: {1-nba_total:.1%})
NHL Cup market totals: {nhl_total:.1%} (gap: {1-nhl_total:.1%})
World Cup top teams total: {wc_total:.1%} (gap: {1-wc_total:.1%})

CRITICAL RULES:
- ONLY make a pick if the verified status above CONFIRMS the team is still alive
- If status is UNKNOWN, say "Need game data — no sports picks until confirmed"
- Do NOT hallucinate series scores or bracket positions
- World Cup "gap" is NOT an arb — you cannot profit by buying all teams since only one wins
- NBA/NHL gap IS meaningful if a team is missing from the market (truly unrepresented)
- Keep under 200 words
- Format: **Market** | Odds | BUY YES/NO | Why (verified fact only)"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        result = response.content[0].text

        if 'need game data' not in result.lower():
            push_to_main_app(
                f"🏀 Sports — {datetime.datetime.now().strftime('%H:%M')}",
                result
            )
        print(f"Sports done")
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

    # Phase 1 — Markets
    print("\n[1/7] Polymarket scan...")
    all_markets = get_all_polymarket_markets(limit=200)
    history = load_market_history()
    movements = detect_odds_movement(all_markets, history)
    save_market_history(all_markets)
    data['markets'] = all_markets
    data['movements'] = movements
    print(f"Markets: {len(all_markets)} | Movements: {len(movements)}")
    time.sleep(5)

    # Phase 2 — Deep political
    print("\n[2/7] Deep political (30 searches)...")
    data['political'] = deep_political_research()
    print("Political done")
    time.sleep(5)

    # Phase 3 — News
    print("\n[3/7] News collection...")
    world_news = get_news(category='world') or []
    pol_news = get_news(category='politics') or []
    sports_news = get_news(category='sports') or []
    biz_news = get_news(category='business') or []
    all_news = world_news + pol_news + sports_news + biz_news
    data['news'] = format_articles(all_news, max=25)
    print(f"News: {len(all_news)} articles")
    time.sleep(5)

    # Phase 4 — VERIFIED sports
    print("\n[4/7] Verified sports research...")
    nba_raw, nba_verified = get_verified_nba_status()
    time.sleep(3)
    nhl_raw, nhl_verified = get_verified_nhl_status()
    data['nba_verified'] = nba_verified
    data['nhl_verified'] = nhl_verified
    data['nba_raw'] = nba_raw
    data['nhl_raw'] = nhl_raw
    print("Sports verified")
    time.sleep(5)

    # Phase 5 — FRED
    print("\n[5/7] FRED data...")
    data['fred'] = get_fred_snapshot()
    print("FRED done")
    time.sleep(5)

    # Phase 6 — Odds movements
    print("\n[6/7] Odds movements...")
    movements_text = ""
    if movements:
        movements_text = "**Odds moved since last check:**\n"
        for mv in movements[:5]:
            arrow = "⬆️" if mv['diff'] > 0 else "⬇️"
            movements_text += f"{arrow} **{mv['title']}**: {mv['old_yes']:.1%} → {mv['new_yes']:.1%} ({mv['diff']:+.1%}) | {mv['end_date']}\n"
    print(f"Movements: {len(movements)}")
    time.sleep(5)

    # Phase 7 — Single synthesis call
    elapsed = time.time() - start_time
    print(f"\n[7/7] Single synthesis call ({elapsed:.0f}s collected)...")

    def fmt(markets, n=25):
        out = ""
        for m in markets[:n]:
            yes = m.get('yes_price')
            vol = m.get('volume', 0)
            end = m.get('end_date', '?')
            days = m.get('days_until', '?')
            floor = " [FLOOR-AVOID]" if m.get('has_floor') else ""
            if yes:
                out += f"• {m['title']}: YES {yes:.1%} | ${vol:,.0f} | {end} ({days}d){floor}\n"
        return out

    # Sports arb
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

    synthesis_prompt = f"""You are Bina giving Nathaniel his intelligence report. SHORT and ACTIONABLE.

Research time: {elapsed:.0f}s | Markets: {len(all_markets)} | News articles: {len(all_news)}

═══ CRITICAL RULES ═══
1. MAX 5 picks total across ALL categories
2. Each pick = exactly one line: **Market** | YES X% | Resolves date (Xd) | BUY YES or BUY NO | H/M/L | One sentence WHY using SPECIFIC data
3. For sports picks: you MUST reference the VERIFIED STATUS below — if status is UNKNOWN, do NOT make a sports pick
4. World Cup "gap" is NOT an arb opportunity — skip it. Only flag NBA/NHL gap if a confirmed finalist team has NO market
5. Skip floor contracts, skip markets resolving >300 days out
6. If odds moved significantly, always flag that first — someone knows something
7. ONE synthesis only — do not contradict yourself
8. Under 250 words total

═══ VERIFIED SPORTS STATUS ═══

NBA VERIFIED FACTS:
{data.get('nba_verified', 'UNKNOWN — do not make NBA picks')}

NHL VERIFIED FACTS:
{data.get('nhl_verified', 'UNKNOWN — do not make NHL picks')}

═══ ALL MARKETS ({len(all_markets)} total) ═══
{fmt(all_markets, 25)}

═══ ODDS MOVEMENTS ═══
{movements_text if movements_text else "No significant movements since last check"}

═══ NBA FINALS MARKET ({len(nba_f)} teams, total {nba_total:.1%}, gap {1-nba_total:.1%}) ═══
{chr(10).join([f"• {m['title'].replace('Will the ','').replace(' win the 2026 NBA Finals?','')}: {m.get('yes_price',0):.1%}" for m in nba_f])}

═══ NHL CUP MARKET ({len(nhl_c)} teams, total {nhl_total:.1%}, gap {1-nhl_total:.1%}) ═══
{chr(10).join([f"• {m['title'].replace('Will the ','').replace(' win the 2026 NHL Stanley Cup?','')}: {m.get('yes_price',0):.1%}" for m in nhl_c])}

═══ POLITICAL RESEARCH (30 searches) ═══
{data.get('political', '')[:2500]}

═══ NEWS ({len(all_news)} articles) ═══
{data.get('news', '')[:1200]}

═══ FRED ECONOMIC DATA ═══
{data.get('fred', '')}

RESPONSE FORMAT — EXACTLY THIS:

## Picks

[If odds moved: ⚡ **ODDS ALERT**: market name moved X% — what it means]

**[Market]** | YES X% | Resolves [date] ([X]d) | BUY YES/NO | H/M/L | [Why with specific data]

[Repeat for each pick, max 5]

## Watch Next 6 Hours

- [Specific event] → affects [specific market]
- [Specific event] → affects [specific market]"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=700,
            messages=[{"role": "user", "content": synthesis_prompt}]
        )
        synthesis = response.content[0].text
        total_time = time.time() - start_time
        print(f"\n✅ Done in {total_time:.0f}s")

        save_memory(f"Research: {synthesis[:400]}", memory_type='research')

        # ONE push for the main report
        push_to_main_app(
            f"🧠 Picks — {datetime.datetime.now().strftime('%H:%M')} ({total_time:.0f}s)",
            synthesis
        )

        # Separate odds movements if significant
        if movements_text:
            push_to_main_app(
                f"⚡ Odds Moved — {datetime.datetime.now().strftime('%H:%M')}",
                movements_text
            )

        # NHL/NBA arb only if gap is real and >8%
        arb_body = ""
        if 1 - nba_total > 0.08 and len(nba_f) >= 2:
            arb_body += f"**NBA Finals gap: {1-nba_total:.1%}**\n"
            arb_body += "This means a team that advanced has no market — that team is free money YES\n"
            for m in nba_f:
                arb_body += f"• {m['title'].replace('Will the ','').replace(' win the 2026 NBA Finals?','')}: {m.get('yes_price',0):.1%}\n"

        if 1 - nhl_total > 0.08 and len(nhl_c) >= 2:
            arb_body += f"\n**NHL Cup gap: {1-nhl_total:.1%}**\n"
            arb_body += "Missing team in market — check who advanced\n"
            for m in nhl_c:
                arb_body += f"• {m['title'].replace('Will the ','').replace(' win the 2026 NHL Stanley Cup?','')}: {m.get('yes_price',0):.1%}\n"

        if arb_body:
            push_to_main_app("⚡ Real Arb Gap Detected", arb_body)

        return synthesis

    except Exception as e:
        print(f"Synthesis error: {str(e)}")
        return None


# ── Scheduler ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("BINA RESEARCH WORKER ONLINE")
    print(f"Started: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
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
