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

# ── Energy & Infrastructure Stock Universe ────────────────────────────────────
# Majors, midstream, utilities, renewables, nuclear
ENERGY_STOCKS = {
    'majors': ['XOM', 'CVX', 'COP', 'SLB', 'HAL', 'OXY', 'BP', 'SHEL', 'TTE', 'EOG'],
    'midstream': ['KMI', 'WMB', 'OKE', 'ET', 'EPD', 'MPLX', 'PAA', 'TRGP'],
    'utilities': ['NEE', 'D', 'SO', 'DUK', 'AEP', 'EXC', 'SRE', 'PCG', 'ED', 'XEL'],
    'renewables': ['ENPH', 'FSLR', 'RUN', 'SEDG', 'BEP', 'CWEN', 'NEP', 'AY'],
    'nuclear': ['CEG', 'VST', 'NRG', 'SMR', 'OKLO', 'NNE'],
    'infrastructure': ['AWK', 'WM', 'RSG', 'AECOM', 'PWR', 'EMN', 'APD']
}

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


# ── FRED ──────────────────────────────────────────────────────────────────────

def get_fred_snapshot():
    if not FRED_API_KEY:
        return ""
    indicators = {
        'FEDFUNDS': 'Fed Rate',
        'UNRATE': 'Unemployment',
        'DGS10': '10yr Treasury',
        'DCOILWTICO': 'WTI Oil',
        'GOLDAMGBD228NLBM': 'Gold',
        'DHHNGSP': 'Natural Gas'
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


# ── Alpha Vantage Stock Data ──────────────────────────────────────────────────

def get_stock_quote(symbol):
    """Get real-time quote for a single stock via Alpha Vantage."""
    if not ALPHA_VANTAGE_KEY:
        return None
    try:
        r = requests.get(
            'https://www.alphavantage.co/query',
            params={
                'function': 'GLOBAL_QUOTE',
                'symbol': symbol,
                'apikey': ALPHA_VANTAGE_KEY
            },
            timeout=10
        )
        if r.status_code == 200:
            data = r.json().get('Global Quote', {})
            if data and data.get('05. price'):
                return {
                    'symbol': symbol,
                    'price': float(data.get('05. price', 0)),
                    'change': float(data.get('09. change', 0)),
                    'change_pct': data.get('10. change percent', '0%').replace('%', ''),
                    'volume': int(data.get('06. volume', 0)),
                    'prev_close': float(data.get('08. previous close', 0))
                }
        return None
    except:
        return None

def get_energy_sector_prices():
    """Pull prices for key energy/infra names. Alpha Vantage free tier = 25 req/day.
    We sample the highest-signal names only."""
    watchlist = ['XOM', 'CVX', 'NEE', 'CEG', 'KMI', 'COP', 'OXY', 'FSLR', 'VST', 'WMB']
    results = []
    for symbol in watchlist:
        quote = get_stock_quote(symbol)
        if quote:
            results.append(quote)
        time.sleep(1.5)  # respect rate limit
    return results

def format_stock_prices(quotes):
    if not quotes:
        return "Stock prices unavailable (Alpha Vantage limit hit — use search fallback).\n"
    output = ""
    for q in quotes:
        pct = float(q.get('change_pct', 0))
        arrow = '📈' if pct > 0 else '📉'
        output += f"{arrow} **{q['symbol']}**: ${q['price']:.2f} | {pct:+.2f}% | Vol: {q['volume']:,}\n"
    return output


# ── Energy Infrastructure Research ───────────────────────────────────────────

def research_energy_sector():
    """Deep research into energy infrastructure stocks and opportunities."""
    print("  Energy sector research...")
    output = ""

    searches = [
        # Macro energy drivers
        "oil price forecast today WTI Brent",
        "natural gas price today LNG",
        "energy sector ETF XLE XLU performance today",
        "electricity demand AI data centers power grid 2025 2026",
        "nuclear energy stocks outlook 2025 2026",

        # Specific catalyst searches
        "Exxon XOM earnings news analyst upgrade downgrade",
        "NextEra Energy NEE solar wind news today",
        "Constellation Energy CEG nuclear data center deal",
        "Kinder Morgan KMI pipeline news today",
        "ConocoPhillips COP oil production news",

        # Macro/geopolitical energy drivers
        "OPEC production decision oil supply news",
        "US LNG export approval news today",
        "power grid infrastructure spending bill news",
        "renewable energy IRA tax credit news today",
        "electricity utility earnings season news",

        # Sector rotation / institutional moves
        "energy stocks institutional buying today hedge fund",
        "energy infrastructure dividend yield comparison today",
        "midstream pipeline MLP distribution news today",
        "solar energy stocks catalyst today FSLR ENPH",
        "uranium nuclear fuel price news today"
    ]

    for i, s in enumerate(searches):
        result = web_search(s, num_results=4)
        output += f"\n{result}"
        time.sleep(1.5)
        if (i + 1) % 5 == 0:
            print(f"    Energy searches: {i+1}/{len(searches)}")

    return output

def research_daily_opportunities():
    """Scan for daily investment opportunities across sectors."""
    print("  Daily opportunities research...")
    output = ""

    searches = [
        # Market-wide opportunity scans
        "stocks with unusual options activity today",
        "stocks making 52-week high today",
        "analyst upgrade target raise today",
        "earnings beat positive guidance today",
        "short squeeze candidates today high short interest",

        # Crypto opportunities
        "Bitcoin price today technical analysis",
        "Ethereum price today catalyst",
        "Solana SOL price news today",
        "crypto market sentiment today",

        # Macro market drivers
        "S&P 500 today market moving news",
        "10 year treasury yield today impact stocks",
        "dollar index DXY today impact commodities",
        "VIX volatility index today market fear",

        # Specific opportunity types
        "IPO this week upcoming news",
        "merger acquisition deal announced today",
        "FDA approval biotech catalyst today",
        "earnings this week big names schedule"
    ]

    for i, s in enumerate(searches):
        result = web_search(s, num_results=4)
        output += f"\n{result}"
        time.sleep(1.5)
        if (i + 1) % 5 == 0:
            print(f"    Opportunity searches: {i+1}/{len(searches)}")

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
    print(f"Breaking news: {datetime.datetime.now().strftime('%H:%M')}")
    triggers = [
        # Energy/infra triggers
        'oil embargo', 'opec emergency', 'pipeline explosion', 'refinery fire',
        'power grid failure', 'electricity shortage', 'gas shortage',
        'strait of hormuz', 'oil supply cut', 'energy crisis',
        'nuclear plant', 'lng terminal', 'oil sanctions',
        # Market triggers
        'federal reserve emergency', 'rate cut surprise', 'rate hike surprise',
        'market crash', 'bank collapse', 'debt default', 'stock circuit breaker',
        'recession confirmed', 'cpi surprise', 'inflation surge',
        # Geopolitical triggers
        'invasion', 'war declared', 'missile launch', 'sanctions',
        'coup', 'assassination attempt', 'nato invoked',
        'taiwan strait', 'china military', 'nuclear',
        # Company-specific triggers
        'exxon mobil acquisition', 'chevron deal', 'nextera earnings',
        'constellation energy deal', 'kinder morgan dividend',
        'sec investigation energy', 'energy company bankruptcy'
    ]

    seen_news = load_seen_news()
    alerts = []

    all_articles = []
    for cat in ['general', 'business']:
        all_articles.extend(get_news(category=cat))
        time.sleep(0.5)

    for query in [
        'breaking energy oil gas news today',
        'stock market crash emergency today',
        'geopolitical crisis breaking today'
    ]:
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

        # Follow up with sector impact
        impact_search = web_search(
            f"stock market impact {alerts[0]['title'][:50]}", num_results=3)
        body += f"**Market impact:**\n{impact_search[:300]}"

        push_to_main_app(
            f"🚨 Breaking: {alerts[0]['title'][:55]}", body)
        save_memory(f"Breaking: {alerts[0]['title']}", memory_type='breaking')
    else:
        print("No breaking alerts")


# ── MAIN INTELLIGENCE REPORT ──────────────────────────────────────────────────

def run_intelligence_report(report_slot):
    """
    report_slot: 'morning' (7am), 'afternoon' (1pm), 'evening' (8pm)
    Each slot has slightly different focus.
    """
    start_time = time.time()
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    slot_labels = {
        'morning':   '☀️ Morning',
        'afternoon': '📊 Afternoon',
        'evening':   '🌙 Evening'
    }
    label = slot_labels.get(report_slot, '🧠')

    print(f"\n{'='*50}")
    print(f"INTELLIGENCE REPORT [{report_slot.upper()}] — {now_str}")
    print(f"{'='*50}")

    data = {}

    # Phase 1 — FRED macro snapshot
    print("\n[1/5] FRED macro data...")
    data['fred'] = get_fred_snapshot()
    print("FRED done")
    time.sleep(3)

    # Phase 2 — Energy sector stock prices
    print("\n[2/5] Energy stock prices...")
    quotes = get_energy_sector_prices()
    data['stock_prices'] = format_stock_prices(quotes)
    data['raw_quotes'] = quotes
    print(f"Prices: {len(quotes)} quotes")
    time.sleep(3)

    # Phase 3 — Energy sector deep research
    print("\n[3/5] Energy sector research (20 searches)...")
    data['energy_research'] = research_energy_sector()
    print("Energy research done")
    time.sleep(5)

    # Phase 4 — Daily investment opportunities
    print("\n[4/5] Daily opportunity scan (17 searches)...")
    data['opportunities'] = research_daily_opportunities()
    print("Opportunity scan done")
    time.sleep(5)

    # Phase 5 — News
    print("\n[5/5] News collection...")
    biz_news = get_news(category='business') or []
    gen_news = get_news(category='general') or []
    all_news = biz_news + gen_news
    data['news'] = format_articles(all_news, max=20)
    print(f"News: {len(all_news)} articles")
    time.sleep(3)

    # Synthesis
    elapsed = time.time() - start_time
    print(f"\nSynthesis call ({elapsed:.0f}s collected)...")

    # Build stock context string
    stock_context = ""
    for q in data.get('raw_quotes', []):
        pct = float(q.get('change_pct', 0))
        arrow = '📈' if pct > 0 else '📉'
        stock_context += f"{arrow} {q['symbol']}: ${q['price']:.2f} ({pct:+.2f}%)\n"

    slot_focus = {
        'morning': "Focus: What to watch TODAY. Flag pre-market movers, overnight news impact, morning setups. Energy stocks to buy/avoid today.",
        'afternoon': "Focus: Midday update. What moved since morning and why. Afternoon setups. Any position adjustments needed.",
        'evening': "Focus: End-of-day recap. What happened today. Best setups for TOMORROW. Overnight watch list. Any after-hours earnings/news."
    }

    synthesis_prompt = f"""You are Bina giving Nathaniel his {report_slot} intelligence report. SHARP and ACTIONABLE.

Report slot: {label} | Research time: {elapsed:.0f}s

{slot_focus.get(report_slot, '')}

═══ CRITICAL RULES ═══
1. MAX 5 investment picks/opportunities
2. Each pick = one line: **TICKER** | $Price | Δ% | BUY/WATCH/AVOID | H/M/L confidence | One sentence why (specific catalyst)
3. Be SPECIFIC — name the catalyst, the price level, the reason
4. Only make picks with a clear catalyst from the research data below
5. Separate picks from general market context
6. Under 300 words total

═══ LIVE STOCK PRICES ═══
{stock_context if stock_context else "Prices unavailable — use search data"}

═══ FRED MACRO DATA ═══
{data.get('fred', 'Unavailable')}

═══ ENERGY SECTOR RESEARCH ═══
{data.get('energy_research', '')[:3000]}

═══ DAILY OPPORTUNITY SCAN ═══
{data.get('opportunities', '')[:2000]}

═══ NEWS ({len(all_news)} articles) ═══
{data.get('news', '')[:1000]}

RESPONSE FORMAT — EXACTLY THIS:

## {label} Intel — {datetime.datetime.now().strftime('%b %d, %Y')}

**Macro:** [2 sentences — key macro context right now]

**Energy Sector:** [2 sentences — what's moving in energy/infra today]

## Picks

**[TICKER]** | $X.XX | +X.X% | BUY/WATCH/AVOID | H/M/L | [Catalyst in one sentence]

[Repeat for each pick, max 5]

## Watch Tonight / Tomorrow

- [Specific thing to watch] → affects [specific ticker]
- [Specific thing to watch] → affects [specific ticker]"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=700,
            messages=[{"role": "user", "content": synthesis_prompt}]
        )
        synthesis = response.content[0].text
        total_time = time.time() - start_time
        print(f"\n✅ {report_slot.upper()} report done in {total_time:.0f}s")

        save_memory(f"Intel report [{report_slot}]: {synthesis[:400]}", memory_type='research')

        push_to_main_app(
            f"{label} Intel — {datetime.datetime.now().strftime('%b %d, %I:%M %p')}",
            synthesis
        )

        return synthesis

    except Exception as e:
        print(f"Synthesis error: {str(e)}")
        return None


# ── Scheduler ─────────────────────────────────────────────────────────────────

def get_la_time():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=-7)

def main():
    print("=" * 50)
    print("BINA RESEARCH WORKER ONLINE")
    print(f"Started: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("  Breaking news:       every 15 min")
    print("  Morning report:      7:00 AM LA")
    print("  Afternoon report:    1:00 PM LA")
    print("  Evening report:      8:00 PM LA")
    print("=" * 50)

    # Track which reports have fired today
    fired_today = {
        'morning': -1,    # day-of-year when last fired
        'afternoon': -1,
        'evening': -1
    }
    last_breaking = 0

    # Fire a startup report immediately so the feed isn't empty on deploy
    print("\nRunning startup intelligence report...")
    try:
        la_now = get_la_time()
        la_hour = la_now.hour
        if la_hour < 12:
            slot = 'morning'
        elif la_hour < 17:
            slot = 'afternoon'
        else:
            slot = 'evening'
        threading.Thread(
            target=run_intelligence_report,
            args=(slot,),
            daemon=True
        ).start()
    except Exception as e:
        print(f"Startup report error: {str(e)}")

    while True:
        try:
            now = time.time()
            la_time = get_la_time()
            la_hour = la_time.hour
            la_minute = la_time.minute
            la_day = la_time.timetuple().tm_yday

            # Breaking news every 15 minutes
            if now - last_breaking > 900:
                last_breaking = now
                threading.Thread(
                    target=run_breaking_news_monitor, daemon=True).start()

            # Morning report — 7:00 AM LA
            if la_hour == 7 and la_minute < 5 and fired_today['morning'] != la_day:
                fired_today['morning'] = la_day
                threading.Thread(
                    target=run_intelligence_report,
                    args=('morning',),
                    daemon=True
                ).start()

            # Afternoon report — 1:00 PM LA
            if la_hour == 13 and la_minute < 5 and fired_today['afternoon'] != la_day:
                fired_today['afternoon'] = la_day
                threading.Thread(
                    target=run_intelligence_report,
                    args=('afternoon',),
                    daemon=True
                ).start()

            # Evening report — 8:00 PM LA
            if la_hour == 20 and la_minute < 5 and fired_today['evening'] != la_day:
                fired_today['evening'] = la_day
                threading.Thread(
                    target=run_intelligence_report,
                    args=('evening',),
                    daemon=True
                ).start()

            time.sleep(60)

        except Exception as e:
            print(f"Scheduler error: {str(e)}")
            time.sleep(60)


if __name__ == '__main__':
    main()
