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

NOTIFICATIONS_FILE = '/tmp/notifications.json'
WATCHLIST_FILE = '/tmp/watchlist.json'
SEEN_NEWS_FILE = '/tmp/seen_news.json'

# Polymarket watchlist — focused on world politics and sports only
POLYMARKET_WATCHLIST = [
    'ukraine', 'russia', 'china', 'taiwan', 'israel', 'iran', 'nato',
    'election', 'trump', 'president', 'war', 'ceasefire', 'invasion',
    'nba', 'finals', 'world cup', 'fifa', 'championship', 'stanley cup',
    'prime minister', 'government', 'congress', 'senate', 'parliament',
    'nuclear', 'military', 'sanction', 'peace', 'treaty', 'coup'
]

# High-value news triggers that should cause immediate alerts
BREAKING_TRIGGERS = [
    'strait of hormuz', 'oil embargo', 'nuclear', 'invasion', 'coup',
    'ceasefire', 'war declared', 'troops deployed', 'sanctions imposed',
    'assassination', 'terror attack', 'fed rate', 'market crash',
    'earthquake', 'tsunami', 'pandemic', 'outbreak', 'defaulted',
    'nato article 5', 'taiwan strait', 'south china sea'
]

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


# ── Notifications (push to main app) ─────────────────────────────────────────

def push_to_main_app(subject, body, notif_type='intelligence'):
    """Push a notification to the main Bina app via its API."""
    try:
        response = requests.post(
            f'{BINA_URL}/internal/add-notification',
            json={
                'id': f'research-{int(time.time())}',
                'type': notif_type,
                'subject': subject,
                'from': 'Bina Research Worker',
                'body': body,
                'draft_reply': '',
                'read': False,
                'timestamp': time.time()
            },
            timeout=10
        )
        if response.status_code == 200:
            print(f"Pushed to main app: {subject}")
        else:
            print(f"Push failed: {response.status_code}")
    except Exception as e:
        print(f"Push error: {str(e)}")


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
            output = ""
            if data.get('answerBox'):
                answer = data['answerBox'].get('answer') or data['answerBox'].get('snippet') or ''
                if answer:
                    output += f"Answer: {answer}\n"
            for r in data.get('organic', [])[:num_results]:
                output += f"• {r.get('title', '')}: {r.get('snippet', '')}\n"
            for n in data.get('news', [])[:3]:
                output += f"• NEWS ({n.get('date', '')}): {n.get('title', '')} — {n.get('snippet', '')}\n"
            return output
        except Exception as e:
            print(f"Search error: {str(e)}")
    return ""


# ── NewsAPI — Real-time news ──────────────────────────────────────────────────

def get_breaking_news(category='world'):
    """Pull real-time news from NewsAPI."""
    if not NEWS_API_KEY:
        return []
    try:
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
            articles = response.json().get('articles', [])
            return [{
                'title': a.get('title', ''),
                'description': a.get('description', ''),
                'source': a.get('source', {}).get('name', ''),
                'published': a.get('publishedAt', ''),
                'url': a.get('url', '')
            } for a in articles if a.get('title')]
        return []
    except Exception as e:
        print(f"NewsAPI error: {str(e)}")
        return []

def get_everything_news(query, from_hours=2):
    """Search NewsAPI for specific topics in last N hours."""
    if not NEWS_API_KEY:
        return []
    try:
        from_time = (datetime.datetime.utcnow() - datetime.timedelta(hours=from_hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
        response = requests.get(
            'https://newsapi.org/v2/everything',
            params={
                'apiKey': NEWS_API_KEY,
                'q': query,
                'language': 'en',
                'sortBy': 'publishedAt',
                'pageSize': 10,
                'from': from_time
            },
            timeout=10
        )
        if response.status_code == 200:
            articles = response.json().get('articles', [])
            return [{
                'title': a.get('title', ''),
                'description': a.get('description', ''),
                'source': a.get('source', {}).get('name', ''),
                'published': a.get('publishedAt', ''),
                'url': a.get('url', '')
            } for a in articles if a.get('title')]
        return []
    except Exception as e:
        print(f"NewsAPI everything error: {str(e)}")
        return []

def format_articles(articles, max=10):
    if not articles:
        return "No articles found."
    output = ""
    for a in articles[:max]:
        output += f"• [{a.get('source', '?')}] {a['title']}"
        if a.get('description'):
            output += f" — {a['description'][:100]}"
        output += f" ({a.get('published', '')[:10]})\n"
    return output


# ── Polymarket — Focused watchlist ───────────────────────────────────────────

def get_watchlist_markets():
    """Get only markets matching our watchlist — world politics and sports."""
    try:
        response = requests.get(
            'https://gamma-api.polymarket.com/markets',
            params={'limit': 100, 'active': 'true', 'closed': 'false'},
            timeout=15
        )
        if response.status_code != 200:
            return []

        markets = response.json()
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        watchlist_markets = []

        for m in markets:
            title = str(m.get('question', m.get('title', ''))).lower()

            # Skip GTA VI floor contracts
            if 'gta vi' in title or 'gta6' in title or 'grand theft auto' in title:
                continue

            # Only include watchlist topics
            if not any(w in title for w in POLYMARKET_WATCHLIST):
                continue

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
            except:
                pass

            # Skip near-certain markets
            if yes_price and (yes_price > 0.92 or yes_price < 0.08):
                continue

            # Get resolution date, skip expired
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

            # Detect floors
            description = m.get('description', '') or ''
            has_floor = any(p in description.lower() for p in ['50-50', '50/50', 'neither', 'resolve 0.5'])

            volume = m.get('volume', 0) or 0
            try:
                volume = float(str(volume).replace(',', ''))
            except:
                volume = 0

            watchlist_markets.append({
                'title': m.get('question', m.get('title', '')),
                'yes_price': yes_price,
                'no_price': no_price,
                'volume': volume,
                'end_date': end_str,
                'days_until': days_until,
                'has_floor': has_floor,
                'description': description[:300]
            })

        # Sort by volume
        watchlist_markets.sort(key=lambda x: x['volume'], reverse=True)
        print(f"Watchlist markets found: {len(watchlist_markets)}")
        return watchlist_markets

    except Exception as e:
        print(f"Polymarket error: {str(e)}")
        return []

def format_watchlist_markets(markets):
    if not markets:
        return "No watchlist markets found."
    output = ""
    for m in markets[:15]:
        yes = m.get('yes_price')
        no = m.get('no_price')
        vol = m.get('volume', 0)
        end = m.get('end_date', '?')
        days = m.get('days_until')
        days_str = f"{days}d" if days is not None else "?"
        floor = " ⚠️ FLOOR CONTRACT" if m.get('has_floor') else ""
        if yes:
            output += f"• {m['title']}\n  YES: {yes:.1%} | NO: {no:.1%} | Vol: ${vol:,.0f} | {end} ({days_str}){floor}\n"
            if m.get('description'):
                output += f"  Rules: {m['description'][:100]}\n"
    return output


# ── NBA Stats ────────────────────────────────────────────────────────────────

def get_nba_playoff_games():
    """Get current NBA playoff games and standings."""
    if not RAPIDAPI_KEY:
        return "RapidAPI key not configured."
    try:
        # Get current season games
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
            output = "**NBA Live/Recent Games:**\n"
            games = data.get('events', data.get('games', []))
            if not games:
                output += "No live games currently.\n"
            for g in games[:5]:
                home = g.get('homeTeam', {})
                away = g.get('awayTeam', {})
                home_name = home.get('teamName', home.get('name', 'Home'))
                away_name = away.get('teamName', away.get('name', 'Away'))
                home_score = home.get('score', '?')
                away_score = away.get('score', '?')
                status = g.get('status', {}).get('type', {}).get('description', 'Unknown')
                output += f"• {away_name} {away_score} @ {home_name} {home_score} — {status}\n"
            return output
        return f"NBA API error: {response.status_code}"
    except Exception as e:
        return f"NBA error: {str(e)}"

def get_nba_team_stats(team_name):
    """Search for specific team stats."""
    try:
        result = web_search(f"{team_name} NBA 2026 playoffs stats performance", num_results=5)
        return result
    except:
        return "Stats unavailable."


# ── Geopolitical Monitor ──────────────────────────────────────────────────────

def monitor_geopolitical_triggers():
    """Check for breaking geopolitical news that could move markets."""
    critical_topics = [
        'strait of hormuz',
        'taiwan strait china military',
        'russia ukraine ceasefire',
        'iran nuclear deal',
        'north korea missile',
        'israel iran attack',
        'nato russia escalation',
        'oil supply disruption',
        'OPEC production cut',
        'federal reserve emergency'
    ]

    alerts = []
    seen_news = load_seen_news()

    for topic in critical_topics:
        articles = get_everything_news(topic, from_hours=4)
        for article in articles:
            title = article.get('title', '')
            if not title or title in seen_news:
                continue

            title_lower = title.lower()
            is_breaking = any(trigger in title_lower for trigger in BREAKING_TRIGGERS)

            if is_breaking:
                alerts.append({
                    'topic': topic,
                    'title': title,
                    'description': article.get('description', ''),
                    'source': article.get('source', ''),
                    'published': article.get('published', '')
                })
                seen_news.add(title)

        time.sleep(0.5)

    save_seen_news(seen_news)
    return alerts


# ── Seen News (prevent duplicate alerts) ─────────────────────────────────────

def load_seen_news():
    try:
        if os.path.exists(SEEN_NEWS_FILE):
            with open(SEEN_NEWS_FILE, 'r') as f:
                data = json.load(f)
                # Only keep news from last 24 hours
                return set(data.get('titles', [])[-500:])
    except:
        pass
    return set()

def save_seen_news(seen):
    try:
        with open(SEEN_NEWS_FILE, 'w') as f:
            json.dump({'titles': list(seen)[-500:]}, f)
    except:
        pass


# ── Deep Research Engine ──────────────────────────────────────────────────────

def run_deep_research():
    """Full deep research cycle — runs every 2 hours during day, once overnight."""
    print(f"Deep research starting at {datetime.datetime.now().strftime('%H:%M')}")
    start_time = time.time()

    report_sections = {}

    # 1. Get watchlist Polymarket markets
    print("Pulling watchlist markets...")
    markets = get_watchlist_markets()
    poly_text = format_watchlist_markets(markets)
    report_sections['polymarket'] = poly_text

    time.sleep(5)

    # 2. World political news — deep
    print("Pulling world news...")
    world_news = get_breaking_news('world')
    political_news = get_breaking_news('politics') if NEWS_API_KEY else []
    world_text = format_articles(world_news + political_news, max=20)
    report_sections['world_news'] = world_text

    time.sleep(5)

    # 3. Deep search on key topics
    print("Deep searching key topics...")
    search_topics = [
        "US foreign policy news today",
        "China Taiwan military news today",
        "Russia Ukraine war update today",
        "Middle East conflict news today",
        "oil prices geopolitical risk today",
        "NBA playoffs 2026 results today",
        "World Cup 2026 qualifying results",
        "Polymarket odds movement today"
    ]
    search_results = ""
    for topic in search_topics:
        result = web_search(topic, num_results=3)
        search_results += f"\n[{topic}]:\n{result}\n"
        time.sleep(1.5)
    report_sections['searches'] = search_results

    time.sleep(5)

    # 4. NBA data
    print("Pulling NBA data...")
    nba_live = get_nba_playoff_games()
    nba_search = web_search("NBA playoffs 2026 series standings who is winning", num_results=5)
    report_sections['nba'] = nba_live + "\n" + nba_search

    time.sleep(5)

    # 5. FRED economic data
    print("Pulling FRED data...")
    fred_data = get_fred_snapshot()
    report_sections['fred'] = fred_data

    time.sleep(5)

    # 6. Synthesize with Claude
    print("Synthesizing...")
    elapsed = time.time() - start_time
    print(f"Data collection took {elapsed:.0f} seconds")

    synthesis_prompt = f"""You are Bina, Nathaniel's AI research engine. You just spent {elapsed:.0f} seconds collecting data. Give him a focused intelligence report.

FOCUS AREAS (in order of priority):
1. World political events that could move Polymarket markets
2. Sports events with active Polymarket markets
3. Breaking geopolitical news with market implications

STRICT RULES:
- NEVER recommend floor contracts (50/50 if neither happens)
- NEVER recommend markets above 90% or below 10%
- ALWAYS include resolution date for every market recommendation
- For sports: cross-reference actual game results with market odds
- For politics: connect specific news to specific markets
- Only recommend when you can explain WHY the price is wrong
- Be specific — name the exact market, the current odds, why they're mispriced

DATA:

POLYMARKET WATCHLIST (world politics + sports only):
{report_sections['polymarket']}

WORLD & POLITICAL NEWS (real-time):
{report_sections['world_news'][:1500]}

DEEP SEARCH RESULTS:
{report_sections['searches'][:2000]}

NBA DATA:
{report_sections['nba'][:600]}

ECONOMIC DATA:
{report_sections['fred'][:400]}

Write like a sharp friend texting. **Bold** key numbers. Under 500 words.

## Top market plays right now
For each: exact market name, YES/NO price, resolution date, your position, confidence (HIGH/MEDIUM/LOW), why the price is wrong in 1-2 sentences using real data above.

## Breaking news with market implications
Any news from the last 4 hours that could move specific markets. Name the market and the direction.

## Sports market edge
Cross-reference actual NBA/World Cup results with current Polymarket odds. Complete the math — if total odds don't add to 100%, flag the arb.

## Watch next 6 hours
Specific events coming up that could create opportunities."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": synthesis_prompt}]
        )
        synthesis = response.content[0].text
        total_time = time.time() - start_time
        print(f"Total research time: {total_time:.0f} seconds")

        # Save to memory
        save_memory(f"Research report: {synthesis[:500]}", memory_type='research')

        # Push to main app
        push_to_main_app(
            f'🧠 Intelligence Update — {datetime.datetime.now().strftime("%H:%M")}',
            synthesis
        )

        # Also push raw market data
        push_to_main_app(
            f'🎯 Watchlist Markets — {datetime.datetime.now().strftime("%H:%M")}',
            poly_text
        )

        print("Research complete and delivered")
        return synthesis

    except Exception as e:
        print(f"Synthesis error: {str(e)}")
        return None


def get_fred_snapshot():
    """Quick FRED data pull."""
    if not FRED_API_KEY:
        return "FRED key not configured."
    indicators = {
        'FEDFUNDS': 'Fed Funds Rate',
        'UNRATE': 'Unemployment',
        'DGS10': '10-Year Treasury',
        'DCOILWTICO': 'WTI Oil'
    }
    output = ""
    for series_id, name in indicators.items():
        try:
            response = requests.get(
                'https://api.stlouisfed.org/fred/series/observations',
                params={'series_id': series_id, 'api_key': FRED_API_KEY,
                        'file_type': 'json', 'limit': 1, 'sort_order': 'desc'},
                timeout=8
            )
            if response.status_code == 200:
                obs = response.json().get('observations', [])
                if obs and obs[0].get('value') != '.':
                    output += f"• {name}: {obs[0]['value']} ({obs[0]['date']})\n"
            time.sleep(0.3)
        except:
            pass
    return output


# ── Breaking News Monitor ─────────────────────────────────────────────────────

def run_breaking_news_monitor():
    """Runs every 15 minutes — checks for breaking news that needs immediate alert."""
    print(f"Breaking news check at {datetime.datetime.now().strftime('%H:%M')}")

    alerts = monitor_geopolitical_triggers()

    if alerts:
        print(f"Found {len(alerts)} breaking alerts!")
        for alert in alerts[:3]:
            # Research the market implications immediately
            search = web_search(f"{alert['title']} market impact polymarket", num_results=3)

            alert_body = f"🚨 **BREAKING: {alert['title']}**\n\n"
            alert_body += f"Source: {alert['source']} | {alert['published'][:16]}\n\n"
            if alert.get('description'):
                alert_body += f"{alert['description']}\n\n"
            alert_body += f"**Market research:**\n{search[:400]}"

            push_to_main_app(
                f"🚨 Breaking: {alert['title'][:50]}",
                alert_body,
                notif_type='intelligence'
            )
            save_memory(f"Breaking news: {alert['title']}", memory_type='breaking')
    else:
        print("No breaking alerts")


# ── Sports Monitor ────────────────────────────────────────────────────────────

def run_sports_monitor():
    """Runs every 30 minutes during game hours — monitors NBA playoffs."""
    print(f"Sports check at {datetime.datetime.now().strftime('%H:%M')}")

    nba_data = get_nba_playoff_games()
    markets = get_watchlist_markets()
    sports_markets = [m for m in markets if any(w in m['title'].lower() for w in
                      ['nba', 'finals', 'spurs', 'okc', 'celtics', 'warriors',
                       'world cup', 'fifa', 'stanley cup'])]

    if not sports_markets:
        print("No active sports markets")
        return

    # Cross-reference game results with odds
    prompt = f"""You are Bina's sports analyst. Cross-reference actual game results with current Polymarket odds.

NBA LIVE DATA:
{nba_data}

ACTIVE SPORTS MARKETS ON POLYMARKET:
{format_watchlist_markets(sports_markets)}

ADDITIONAL SEARCH:
{web_search('NBA playoff results today 2026 series update', num_results=5)}

Find any mismatch between actual results and current odds. For example if a team just won a game and their championship odds haven't updated yet, that's an edge.

Give me: exact market, current odds, actual game state, your recommendation, confidence. Under 200 words. If no edge found, just say "No sports edge right now" and stop."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        result = response.content[0].text
        if 'no sports edge' not in result.lower():
            push_to_main_app(
                f'🏀 Sports Market Update — {datetime.datetime.now().strftime("%H:%M")}',
                result
            )
        print(f"Sports check done: {result[:100]}")
    except Exception as e:
        print(f"Sports analysis error: {str(e)}")


# ── Scheduler ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("BINA RESEARCH WORKER ONLINE")
    print(f"Started at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    last_deep_research = 0
    last_breaking_check = 0
    last_sports_check = 0
    last_daily_report = -1

    # Run immediately on startup
    print("Running initial research...")
    try:
        run_deep_research()
    except Exception as e:
        print(f"Initial research error: {str(e)}")

    while True:
        try:
            now = time.time()
            la_time = datetime.datetime.utcnow() + datetime.timedelta(hours=-7)
            la_hour = la_time.hour
            la_day = la_time.timetuple().tm_yday

            # Breaking news check every 15 minutes
            if now - last_breaking_check > 900:
                last_breaking_check = now
                threading.Thread(target=run_breaking_news_monitor, daemon=True).start()

            # Sports check every 30 minutes between 4pm-midnight LA time
            if now - last_sports_check > 1800 and 16 <= la_hour <= 24:
                last_sports_check = now
                threading.Thread(target=run_sports_monitor, daemon=True).start()

            # Deep research every 2 hours
            if now - last_deep_research > 7200:
                last_deep_research = now
                threading.Thread(target=run_deep_research, daemon=True).start()

            # Daily overnight deep research at midnight
            if la_hour == 0 and la_day != last_daily_report:
                last_daily_report = la_day
                print("Midnight — running overnight deep research")
                threading.Thread(target=run_deep_research, daemon=True).start()

            time.sleep(60)

        except Exception as e:
            print(f"Scheduler error: {str(e)}")
            time.sleep(60)


if __name__ == '__main__':
    main()
