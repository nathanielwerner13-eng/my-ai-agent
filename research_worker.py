import os
import json
import time
import datetime
import threading
import requests
import uuid
from anthropic import Anthropic
from pinecone import Pinecone

# ── Setup ─────────────────────────────────────────────────────────────────────

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
            headers={'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'},
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


# ── Push to main app ──────────────────────────────────────────────────────────

def push_to_main_app(subject, body, notif_type='intelligence', priority=False):
    try:
        notif_id = f'research-{int(time.time())}-{uuid.uuid4().hex[:6]}'
        response = requests.post(
            f'{BINA_URL}/internal/add-notification',
            json={
                'id': notif_id,
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
            print(f"❌ Push failed {response.status_code}: {subject[:40]}")
    except Exception as e:
        print(f"Push error: {str(e)}")


# ── Web Search with full content ──────────────────────────────────────────────

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
            answer = data['answerBox'].get('answer') or data['answerBox'].get('snippet') or ''
            if answer:
                output += f"DIRECT: {answer}\n"
        for r in data.get('organic', [])[:num_results]:
            output += f"• {r.get('title', '')}: {r.get('snippet', '')}\n"
        for n in data.get('news', [])[:4]:
            output += f"• NEWS ({n.get('date', 'recent')}): {n.get('title', '')} — {n.get('snippet', '')}\n"
        return output
    except Exception as e:
        return f"Search error: {str(e)}"

def fetch_article(url):
    """Fetch actual article content."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; BinaBot/1.0)'}
        response = requests.get(url, headers=headers, timeout=8)
        if response.status_code == 200:
            # Basic text extraction
            text = response.text
            # Remove HTML tags roughly
            import re
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            return text[:3000]
        return ""
    except:
        return ""


# ── NewsAPI ───────────────────────────────────────────────────────────────────

def get_news(category='general', query=None, hours=6):
    if not NEWS_API_KEY:
        return []
    try:
        if query:
            from_time = (datetime.datetime.utcnow() - datetime.timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
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
    if not articles:
        return "No articles."
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


# ── Polymarket — Full scan ────────────────────────────────────────────────────

def get_all_polymarket_markets(limit=200):
    """Get ALL markets, not just watchlist — then filter intelligently."""
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

            # Skip GTA VI floor contracts
            title_lower = title.lower()
            if any(skip in title_lower for skip in ['gta vi', 'gta6', 'grand theft auto']):
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
                    end_dt = datetime.datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                    days_until = (end_dt - now_utc).days
                    if days_until < 0:
                        continue
                    end_str = end_dt.strftime('%b %d, %Y')
                except:
                    pass

            description = (m.get('description', '') or '')[:500]
            has_floor = any(p in description.lower() for p in
                          ['50-50', '50/50', 'neither', 'resolve 0.5', '0.50'])

            # Skip near-certain and floor contracts
            if yes_price and (yes_price > 0.93 or yes_price < 0.07):
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
        return processed

    except Exception as e:
        print(f"Polymarket error: {str(e)}")
        return []

def find_interesting_markets(markets, news_keywords):
    """Find markets that relate to current news — dynamic not just watchlist."""
    interesting = []
    news_lower = ' '.join(news_keywords).lower()

    for m in markets:
        title_lower = m['title'].lower()
        tags_lower = ' '.join(m.get('tags', [])).lower()
        all_text = title_lower + ' ' + tags_lower

        # Check if market relates to current news
        relevance_score = 0
        for keyword in news_keywords:
            if keyword.lower() in all_text:
                relevance_score += 1

        if relevance_score > 0:
            m['relevance'] = relevance_score
            interesting.append(m)

    interesting.sort(key=lambda x: x.get('relevance', 0), reverse=True)
    return interesting[:20]


# ── NBA Real Data ─────────────────────────────────────────────────────────────

def get_nba_data():
    """Pull real NBA data — live scores, series status, player stats."""
    output = "**NBA Playoff Data:**\n"

    # Try RapidAPI first
    if RAPIDAPI_KEY:
        try:
            # Get live games
            response = requests.get(
                'https://nba-api-free-data.p.rapidapi.com/nba-live-score',
                headers={
                    'x-rapidapi-host': 'nba-api-free-data.p.rapidapi.com',
                    'x-rapidapi-key': RAPIDAPI_KEY
                },
                timeout=10
            )
            print(f"NBA API status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print(f"NBA data keys: {list(data.keys())[:5]}")
                # Try multiple possible response formats
                games = (data.get('events') or data.get('games') or
                        data.get('scoreboard', {}).get('games') or [])
                if games:
                    output += f"Live/Recent Games ({len(games)} found):\n"
                    for g in games[:5]:
                        try:
                            home = g.get('homeTeam', g.get('home', {}))
                            away = g.get('awayTeam', g.get('away', {}))
                            home_name = (home.get('teamName') or home.get('name') or
                                       home.get('teamTricode') or 'Home')
                            away_name = (away.get('teamName') or away.get('name') or
                                       away.get('teamTricode') or 'Away')
                            home_score = home.get('score', '?')
                            away_score = away.get('score', '?')
                            status = (g.get('status', {}).get('type', {}).get('description') or
                                    g.get('statusText') or g.get('status') or 'Unknown')
                            output += f"  {away_name} {away_score} @ {home_name} {home_score} — {status}\n"
                        except:
                            pass
                else:
                    output += "No live games via API — using search backup\n"
        except Exception as e:
            print(f"NBA API error: {str(e)}")
            output += f"NBA API error: {str(e)}\n"

    # Always supplement with search for series context
    searches = [
        "NBA Finals 2026 series score who is winning",
        "NBA playoffs 2026 conference finals results today",
        "OKC Thunder Spurs Knicks NBA Finals 2026 bracket",
    ]
    for s in searches:
        result = web_search(s, num_results=4)
        output += f"\n{result}"
        time.sleep(1)

    return output

def get_nhl_data():
    """NHL playoff data."""
    output = "**NHL Playoff Data:**\n"
    searches = [
        "NHL Stanley Cup playoffs 2026 series scores today",
        "Colorado Avalanche Carolina Hurricanes NHL playoffs 2026",
        "NHL conference finals 2026 who is winning series"
    ]
    for s in searches:
        result = web_search(s, num_results=4)
        output += f"\n{result}"
        time.sleep(1)
    return output

def get_world_cup_data():
    """FIFA World Cup 2026 data."""
    output = "**FIFA World Cup 2026:**\n"
    searches = [
        "FIFA World Cup 2026 group draw results teams",
        "World Cup 2026 favorites odds predictions",
        "France Spain England World Cup 2026 form results"
    ]
    for s in searches:
        result = web_search(s, num_results=4)
        output += f"\n{result}"
        time.sleep(1)
    return output


# ── Deep Political Research ───────────────────────────────────────────────────

def deep_political_research():
    """Comprehensive political research — 20+ searches."""
    output = ""
    topics = [
        # US Politics
        "Trump major policy announcement today",
        "US Congress vote legislation today",
        "2026 midterm election news polling",
        "Trump approval rating latest poll",
        # World Politics
        "China Taiwan military news today",
        "Russia Ukraine war ceasefire news today",
        "Israel Iran nuclear deal news",
        "Middle East conflict breaking news",
        "NATO Russia tensions news today",
        "North Korea missile launch news",
        # Geopolitical
        "Strait of Hormuz oil shipping news",
        "OPEC oil production decision",
        "Saudi Arabia geopolitical news",
        "India Pakistan tensions news",
        "South China Sea military news",
        # Economics that affect politics
        "Federal Reserve interest rate decision",
        "US inflation data CPI today",
        "Stock market crash or rally news",
        # Political prediction markets specifically
        "Polymarket political odds movement today",
        "prediction market political news today"
    ]

    for topic in topics:
        result = web_search(topic, num_results=3)
        output += f"\n{result}"
        time.sleep(1.2)

    return output


# ── Market History (track odds movement) ─────────────────────────────────────

def load_market_history():
    try:
        if os.path.exists(MARKET_HISTORY_FILE):
            with open(MARKET_HISTORY_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    return {}

def save_market_history(history):
    try:
        with open(MARKET_HISTORY_FILE, 'w') as f:
            json.dump(history, f)
    except:
        pass

def detect_odds_movement(markets, history):
    """Find markets where odds moved significantly since last check."""
    movements = []
    new_history = {}

    for m in markets:
        title = m['title']
        yes = m.get('yes_price')
        if not yes:
            continue

        new_history[title] = {'yes': yes, 'time': time.time()}

        if title in history:
            old_yes = history[title].get('yes', yes)
            diff = yes - old_yes
            if abs(diff) > 0.03:  # More than 3% move
                movements.append({
                    'title': title,
                    'old_yes': old_yes,
                    'new_yes': yes,
                    'diff': diff,
                    'volume': m.get('volume', 0),
                    'end_date': m.get('end_date', '?'),
                    'days_until': m.get('days_until')
                })

    save_market_history(new_history)
    movements.sort(key=lambda x: abs(x['diff']), reverse=True)
    return movements


# ── FRED Data ─────────────────────────────────────────────────────────────────

def get_fred_snapshot():
    if not FRED_API_KEY:
        return ""
    indicators = {
        'FEDFUNDS': 'Fed Funds Rate',
        'UNRATE': 'Unemployment',
        'DGS10': '10-Year Treasury',
        'DCOILWTICO': 'WTI Oil',
        'GOLDAMGBD228NLBM': 'Gold (London Fix)'
    }
    output = "**Federal Reserve Data:**\n"
    for series_id, name in indicators.items():
        try:
            response = requests.get(
                'https://api.stlouisfed.org/fred/series/observations',
                params={'series_id': series_id, 'api_key': FRED_API_KEY,
                        'file_type': 'json', 'limit': 2, 'sort_order': 'desc'},
                timeout=8
            )
            if response.status_code == 200:
                obs = response.json().get('observations', [])
                if len(obs) >= 2 and obs[0].get('value') != '.':
                    current = float(obs[0]['value'])
                    previous = float(obs[1]['value']) if obs[1].get('value') != '.' else current
                    change = current - previous
                    arrow = '📈' if change > 0 else '📉' if change < 0 else '➡️'
                    output += f"• {arrow} **{name}**: {current} (prev: {previous}, Δ{change:+.2f}) as of {obs[0]['date']}\n"
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


# ── Breaking News Monitor ─────────────────────────────────────────────────────

def run_breaking_news_monitor():
    """Runs every 15 min — broad triggers not just extreme events."""
    print(f"Breaking news check at {datetime.datetime.now().strftime('%H:%M')}")

    # Broader triggers — market-moving not just catastrophic
    market_moving_keywords = [
        # Geopolitical
        'strait of hormuz', 'taiwan strait', 'china military', 'invasion',
        'nuclear', 'ceasefire', 'war declared', 'troops deployed',
        'sanctions', 'nato', 'missile launch', 'attack on',
        # Economic
        'federal reserve emergency', 'rate hike surprise', 'rate cut surprise',
        'market crash', 'circuit breaker', 'bank collapse', 'default',
        'oil embargo', 'opec cut', 'opec increase',
        # Political
        'trump impeachment', 'trump resignation', 'assassination attempt',
        'coup attempt', 'election fraud', 'martial law',
        'president resign', 'prime minister resign',
        # Sports (for active playoff markets)
        'nba finals game', 'stanley cup game', 'world cup result',
        'eliminated playoffs', 'advances finals', 'series tied',
        'upset win', 'blowout loss'
    ]

    seen_news = load_seen_news()
    breaking_alerts = []

    # Check multiple news categories
    categories = ['general', 'politics', 'business', 'sports']
    all_articles = []
    for cat in categories:
        articles = get_news(category=cat)
        all_articles.extend(articles)
        time.sleep(0.5)

    # Also search specific high-value topics
    high_value_queries = [
        'breaking news geopolitical crisis today',
        'NBA finals result today score',
        'Stanley Cup game result today',
        'oil price spike news today'
    ]
    for query in high_value_queries:
        articles = get_news(query=query, hours=2)
        all_articles.extend(articles)
        time.sleep(0.5)

    for article in all_articles:
        title = article.get('title', '')
        if not title or '[Removed]' in title or title in seen_news:
            continue

        title_lower = title.lower()
        desc_lower = (article.get('description', '') or '').lower()
        combined = title_lower + ' ' + desc_lower

        matched_keywords = [kw for kw in market_moving_keywords if kw in combined]

        if matched_keywords:
            breaking_alerts.append({
                'title': title,
                'description': article.get('description', ''),
                'source': article.get('source', {}).get('name', '?'),
                'published': article.get('publishedAt', '')[:16],
                'url': article.get('url', ''),
                'keywords': matched_keywords
            })
            seen_news.add(title)

    save_seen_news(seen_news)

    if breaking_alerts:
        print(f"Found {len(breaking_alerts)} breaking alerts!")
        # Group into single notification
        body = f"🚨 **{len(breaking_alerts)} Market-Moving Stories Detected**\n\n"

        for alert in breaking_alerts[:5]:
            body += f"**{alert['title']}**\n"
            body += f"Source: {alert['source']} | {alert['published']}\n"
            if alert.get('description'):
                body += f"{alert['description'][:150]}\n"
            body += f"Triggers: {', '.join(alert['keywords'][:3])}\n\n"

        # Research market implications
        if breaking_alerts:
            top_alert = breaking_alerts[0]
            market_research = web_search(
                f"Polymarket odds {top_alert['title'][:50]} market impact",
                num_results=3
            )
            body += f"**Market implications:**\n{market_research[:400]}"

        push_to_main_app(
            f"🚨 Breaking: {breaking_alerts[0]['title'][:50]}",
            body,
            priority=True
        )

        # Save to memory
        save_memory(f"Breaking news: {breaking_alerts[0]['title']}", memory_type='breaking')
    else:
        print("No breaking alerts this cycle")


# ── Sports Deep Monitor ───────────────────────────────────────────────────────

def run_sports_monitor():
    """Deep sports research — cross-reference live data with market odds."""
    print(f"Sports monitor at {datetime.datetime.now().strftime('%H:%M')}")

    # Get all markets
    all_markets = get_all_polymarket_markets(limit=200)
    sports_keywords = ['nba', 'nhl', 'finals', 'stanley cup', 'world cup', 'fifa',
                      'spurs', 'thunder', 'knicks', 'avalanche', 'hurricanes',
                      'france', 'spain', 'england', 'brazil', 'argentina']
    sports_markets = [m for m in all_markets if any(
        kw in m['title'].lower() for kw in sports_keywords)]

    if not sports_markets:
        print("No sports markets found")
        return

    # Get real data
    nba_data = get_nba_data()
    nhl_data = get_nhl_data()

    # World Cup arb calculation
    wc_markets = [m for m in sports_markets if 'world cup' in m['title'].lower() or
                 'fifa' in m['title'].lower()]
    arb_text = ""
    if wc_markets:
        total_yes = sum(m.get('yes_price', 0) for m in wc_markets if m.get('yes_price'))
        gap = 1.0 - total_yes
        arb_text = f"\n**World Cup Arb:**\n"
        arb_text += f"Total YES across {len(wc_markets)} teams: {total_yes:.1%}\n"
        arb_text += f"Gap: {gap:.1%}\n"
        for m in sorted(wc_markets, key=lambda x: x.get('yes_price', 0), reverse=True)[:8]:
            yes = m.get('yes_price', 0)
            arb_text += f"• {m['title']}: {yes:.1%}\n"
        if gap > 0.05:
            arb_text += f"⚡ **{gap:.1%} EV gap — field bet opportunity**\n"

    # NBA arb
    nba_finals_markets = [m for m in sports_markets if 'nba finals' in m['title'].lower()]
    nba_arb = ""
    if nba_finals_markets:
        total = sum(m.get('yes_price', 0) for m in nba_finals_markets if m.get('yes_price'))
        gap = 1.0 - total
        nba_arb = f"\n**NBA Finals Arb:**\nTotal YES: {total:.1%} | Gap: {gap:.1%}\n"
        for m in nba_finals_markets:
            nba_arb += f"• {m['title']}: YES {m.get('yes_price', 0):.1%}\n"

    # Synthesize sports intelligence
    prompt = f"""You are Bina's sports analyst. Cross-reference real game data with prediction market odds to find genuine edges.

LIVE NBA DATA (including search results):
{nba_data[:1500]}

NHL DATA:
{nhl_data[:800]}

ACTIVE SPORTS MARKETS ON POLYMARKET:
{chr(10).join([f"• {m['title']}: YES {m.get('yes_price', 0):.1%} | Vol ${m.get('volume', 0):,.0f} | Resolves {m.get('end_date', '?')} ({m.get('days_until', '?')}d)" for m in sports_markets[:15]])}

WORLD CUP ARB ANALYSIS:
{arb_text}

NBA ARB:
{nba_arb}

Find SPECIFIC edges where real-world data contradicts current odds. Complete ALL math.

Rules:
- Cross-reference actual series scores with championship odds
- If a team just won/lost a game, check if odds have adjusted
- Complete World Cup and NBA arb math fully
- Only recommend if you have ACTUAL game data to back it

Format: Market name | Current odds | Real data | Your call | Why | Confidence
Under 300 words. If genuinely no edge, say exactly why for each market."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        result = response.content[0].text

        body = f"{result}\n\n{arb_text}\n{nba_arb}"
        push_to_main_app(
            f"🏀 Sports Intelligence — {datetime.datetime.now().strftime('%H:%M')}",
            body
        )
        print(f"Sports report delivered")
    except Exception as e:
        print(f"Sports synthesis error: {str(e)}")


# ── MAIN DEEP RESEARCH (20-30 min cycle) ─────────────────────────────────────

def run_deep_research():
    """Full deep research — minimum 20 minutes of actual work."""
    start_time = time.time()
    print(f"\n{'='*50}")
    print(f"DEEP RESEARCH STARTING — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")

    data = {}

    # Phase 1 — Get all Polymarket markets (2 min)
    print("\n[1/7] Scanning all Polymarket markets...")
    all_markets = get_all_polymarket_markets(limit=200)
    market_history = load_market_history()
    odds_movements = detect_odds_movement(all_markets, market_history)
    data['markets'] = all_markets
    data['movements'] = odds_movements
    print(f"Found {len(all_markets)} markets, {len(odds_movements)} with significant odds movement")
    time.sleep(5)

    # Phase 2 — Deep political research (8 min)
    print("\n[2/7] Running deep political research (20+ searches)...")
    political_data = deep_political_research()
    data['political'] = political_data
    print("Political research complete")
    time.sleep(5)

    # Phase 3 — News collection (3 min)
    print("\n[3/7] Collecting news from multiple categories...")
    world_news = get_news(category='world') if NEWS_API_KEY else []
    political_news = get_news(category='politics') if NEWS_API_KEY else []
    sports_news = get_news(category='sports') if NEWS_API_KEY else []
    business_news = get_news(category='business') if NEWS_API_KEY else []
    all_news = world_news + political_news + sports_news + business_news
    data['news'] = format_articles(all_news, max=30)
    print(f"Collected {len(all_news)} articles")
    time.sleep(5)

    # Phase 4 — Sports data (5 min)
    print("\n[4/7] Deep sports research...")
    data['nba'] = get_nba_data()
    time.sleep(3)
    data['nhl'] = get_nhl_data()
    time.sleep(3)
    data['world_cup'] = get_world_cup_data()
    print("Sports research complete")
    time.sleep(5)

    # Phase 5 — Economic data (2 min)
    print("\n[5/7] Economic data...")
    data['fred'] = get_fred_snapshot()
    print("Economic data complete")
    time.sleep(5)

    # Phase 6 — Find news-related markets (2 min)
    print("\n[6/7] Cross-referencing news with markets...")
    # Extract keywords from news
    news_keywords = []
    for article in (world_news + political_news)[:20]:
        title = article.get('title', '').lower()
        for keyword in ['ukraine', 'russia', 'china', 'taiwan', 'iran', 'israel',
                       'trump', 'nato', 'election', 'war', 'ceasefire', 'nuclear',
                       'oil', 'fed', 'inflation', 'congress']:
            if keyword in title:
                news_keywords.append(keyword)
    news_keywords = list(set(news_keywords))

    relevant_markets = find_interesting_markets(all_markets, news_keywords)
    data['relevant_markets'] = relevant_markets
    print(f"Found {len(relevant_markets)} news-relevant markets")
    time.sleep(5)

    # Phase 7 — Synthesize (3 min)
    elapsed = time.time() - start_time
    print(f"\n[7/7] Synthesizing... (collected for {elapsed:.0f}s)")

    # Format markets for analysis
    def fmt_markets(markets, limit=15):
        output = ""
        for m in markets[:limit]:
            yes = m.get('yes_price')
            vol = m.get('volume', 0)
            end = m.get('end_date', '?')
            days = m.get('days_until')
            floor = " ⚠️FLOOR" if m.get('has_floor') else ""
            if yes:
                output += f"• {m['title']}: YES {yes:.1%} | ${vol:,.0f} | {end} ({days}d){floor}\n"
        return output

    # Format odds movements
    movements_text = ""
    if odds_movements:
        movements_text = "**ODDS MOVEMENTS (since last check):**\n"
        for mv in odds_movements[:5]:
            direction = "⬆️" if mv['diff'] > 0 else "⬇️"
            movements_text += f"{direction} {mv['title']}: {mv['old_yes']:.1%} → {mv['new_yes']:.1%} ({mv['diff']:+.1%})\n"

    # World Cup arb
    wc_markets = [m for m in all_markets if 'world cup' in m['title'].lower()
                 and m.get('yes_price') and m.get('yes_price') > 0.05]
    wc_total = sum(m.get('yes_price', 0) for m in wc_markets)
    wc_gap = 1.0 - wc_total

    # NBA Finals arb
    nba_finals = [m for m in all_markets if 'nba finals' in m['title'].lower() and m.get('yes_price')]
    nba_total = sum(m.get('yes_price', 0) for m in nba_finals)
    nba_gap = 1.0 - nba_total

    synthesis_prompt = f"""You are Bina, Nathaniel's AI research engine. You just spent {elapsed:.0f} seconds collecting data across 7 research phases. Give him a comprehensive intelligence report.

RESEARCH TIME: {elapsed:.0f} seconds | Searches: 25+ | Markets analyzed: {len(all_markets)}

STRICT RULES:
1. NEVER recommend floor contracts (50/50 if neither happens by deadline)
2. NEVER recommend markets above 93% or below 7%
3. ALWAYS include resolution date and days remaining
4. For sports: cross-reference actual game results with odds — complete the math
5. For politics: connect specific news to specific markets with reasoning
6. If odds moved significantly since last check, flag it — someone knows something
7. Complete ALL arb calculations — don't say "need more data"
8. Be specific — name markets, state odds, explain the edge in one clear sentence

POLYMARKET — TOP MARKETS BY VOLUME:
{fmt_markets(all_markets, 20)}

NEWS-RELEVANT MARKETS (connected to today's news):
{fmt_markets(relevant_markets, 10)}

ODDS MOVEMENTS DETECTED:
{movements_text if movements_text else "No significant movements since last check"}

WORLD CUP ARB: {len(wc_markets)} teams | Total YES: {wc_total:.1%} | Gap: {wc_gap:.1%}
{chr(10).join([f"• {m['title']}: {m.get('yes_price', 0):.1%}" for m in sorted(wc_markets, key=lambda x: x.get('yes_price', 0), reverse=True)[:8]])}

NBA FINALS ARB: {len(nba_finals)} teams | Total YES: {nba_total:.1%} | Gap: {nba_gap:.1%}
{chr(10).join([f"• {m['title']}: {m.get('yes_price', 0):.1%}" for m in nba_finals])}

NBA LIVE DATA:
{data.get('nba', '')[:1000]}

NHL DATA:
{data.get('nhl', '')[:600]}

WORLD CUP DATA:
{data.get('world_cup', '')[:600]}

POLITICAL DEEP RESEARCH:
{data.get('political', '')[:2000]}

REAL-TIME NEWS ({len(all_news)} articles):
{data.get('news', '')[:1500]}

FRED ECONOMIC DATA:
{data.get('fred', '')}

Write like a sharp friend who just spent 30 minutes researching. **Bold** key numbers and market names. Under 600 words.

## 🎯 Top Plays Right Now
For each: **Market name** | Current YES% | Resolves: date (Xd) | Position: YES/NO | Confidence: H/M/L | Edge: one sentence why price is wrong

## ⚡ Odds Movement Alert
If any market moved >3% since last check, flag it — this means someone has information

## 🌍 Political Market Edge
Connect today's specific political news to specific markets. Name the market and explain the mispricing.

## 🏀⚽ Sports Arb
Complete World Cup and NBA math. State exact arb opportunity if gap >5%.

## 📰 Breaking Context
Top 3 stories from today's news and which markets they affect.

## 🔮 Next 6 Hours
What to watch and which markets could move."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": synthesis_prompt}]
        )
        synthesis = response.content[0].text
        total_time = time.time() - start_time
        print(f"\n✅ Deep research complete in {total_time:.0f} seconds")

        save_memory(f"Deep research: {synthesis[:500]}", memory_type='research')

        # Push main intelligence report
        push_to_main_app(
            f"🧠 Intelligence Report — {datetime.datetime.now().strftime('%H:%M')} ({total_time:.0f}s research)",
            synthesis
        )

        # Push separate market data
        markets_body = f"**All Active Markets ({len(all_markets)} found):**\n\n"
        markets_body += fmt_markets(all_markets, 25)
        if movements_text:
            markets_body += f"\n{movements_text}"
        push_to_main_app(
            f"🎯 Market Scan — {len(all_markets)} markets analyzed",
            markets_body
        )

        # Push arb opportunities if significant
        if wc_gap > 0.05 or nba_gap > 0.05:
            arb_body = ""
            if wc_gap > 0.05:
                arb_body += f"**⚡ World Cup Arb: {wc_gap:.1%} gap**\n"
                for m in sorted(wc_markets, key=lambda x: x.get('yes_price', 0), reverse=True):
                    arb_body += f"• {m['title']}: {m.get('yes_price', 0):.1%}\n"
                arb_body += f"Total: {wc_total:.1%} | Gap: {wc_gap:.1%}\n\n"
            if nba_gap > 0.05:
                arb_body += f"**⚡ NBA Finals Arb: {nba_gap:.1%} gap**\n"
                for m in nba_finals:
                    arb_body += f"• {m['title']}: {m.get('yes_price', 0):.1%}\n"
                arb_body += f"Total: {nba_total:.1%} | Gap: {nba_gap:.1%}\n"
            if arb_body:
                push_to_main_app("⚡ Arb Opportunity Detected", arb_body, priority=True)

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
    print("  • Breaking news: every 15 min")
    print("  • Sports monitor: every 30 min (4pm-1am LA)")
    print("  • Deep research: every 3 hours")
    print("=" * 50)

    last_deep = 0
    last_breaking = 0
    last_sports = 0

    # Run immediately on startup
    print("\nRunning initial deep research on startup...")
    try:
        threading.Thread(target=run_deep_research, daemon=True).start()
    except Exception as e:
        print(f"Initial research error: {str(e)}")

    while True:
        try:
            now = time.time()
            la_time = datetime.datetime.utcnow() + datetime.timedelta(hours=-7)
            la_hour = la_time.hour

            # Breaking news every 15 minutes
            if now - last_breaking > 900:
                last_breaking = now
                threading.Thread(target=run_breaking_news_monitor, daemon=True).start()

            # Sports every 30 minutes during game hours (4pm-1am LA)
            if now - last_sports > 1800 and (la_hour >= 16 or la_hour <= 1):
                last_sports = now
                threading.Thread(target=run_sports_monitor, daemon=True).start()

            # Deep research every 3 hours
            if now - last_deep > 10800:
                last_deep = now
                threading.Thread(target=run_deep_research, daemon=True).start()

            time.sleep(60)

        except Exception as e:
            print(f"Scheduler error: {str(e)}")
            time.sleep(60)


if __name__ == '__main__':
    main()
