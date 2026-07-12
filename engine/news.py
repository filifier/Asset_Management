"""
news.py — financial news via official RSS feeds (no HTML scraping).

Six sources, all verified reachable and fresh via RSS as of 2026-07:
Yahoo Finance, Investing.com, MarketWatch, CNBC, Seeking Alpha, and
Reuters (which discontinued its own RSS — read through Google News RSS
restricted to reuters.com, itself a plain RSS feed).

We deliberately consume ONLY the syndication feeds (headline + link +
timestamp), never the article HTML: it's the access channel publishers
expose for exactly this use, it's stable against redesigns, and we
republish nothing beyond headline + source + link (aggregator-style,
like Google News), which keeps us clear of copyright issues. Full-text
NLP on articles would be a different (and legally murkier) project.

Output (docs/data/news.json): items tagged with the portfolio-universe
tickers and macro topics they mention, so the front-end can rank them
against the user's actual holdings — the ranking happens client-side
because each user's portfolio lives in their browser/Supabase session,
not in this build step.
"""

import re
import json
import datetime as dt
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# Some feeds (Investing.com behind Cloudflare) reject non-browser agents.
NEWS_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

FEEDS = [
    ("Yahoo Finance",  "https://finance.yahoo.com/news/rssindex"),
    ("Investing.com",  "https://www.investing.com/rss/news_25.rss"),
    ("MarketWatch",    "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("CNBC",           "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("CNBC",           "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("Seeking Alpha",  "https://seekingalpha.com/market_currents.xml"),
    ("Reuters",        "https://news.google.com/rss/search?q=site:reuters.com+"
                       "(markets%20OR%20stocks%20OR%20fed%20OR%20economy%20OR%20inflation)"
                       "&hl=en-US&gl=US&ceid=US:en"),
]

MAX_AGE_DAYS = 7
MAX_ITEMS = 120

# Macro topic taxonomy: slug -> regex (matched on lowercased headline).
# Slugs are stable keys the front-end maps to Italian labels.
TOPICS = {
    "tassi":       r"\bfed\b|federal reserve|interest rate|rate (cut|hike)|treasur|yield|\becb\b|\bbce\b|central bank|powell|lagarde",
    "inflazione":  r"inflation|\bcpi\b|consumer price",
    "petrolio":    r"\boil\b|crude|opec",
    "oro":         r"\bgold\b",
    "valute":      r"\bdollar\b|\beuro\b|currenc|forex|\byen\b",
    "volatilita":  r"\bvix\b|volatilit|sell-?off|correction|crash|rout\b",
    "ai-chip":     r"\ba\.?i\.?\b|artificial intelligence|\bchip|semiconductor",
    "energia":     r"clean energy|renewable|solar|wind power|electric vehicle",
    "utili":       r"earnings|quarterly result|guidance|profit",
    "cripto":      r"bitcoin|crypto|ethereum",
    "azionario":   r"s&p ?500|nasdaq|dow jones|wall street|stock market|stocks\b",
    "geopolitica": r"tariff|trade war|sanction|geopolit|election",
}
_TOPIC_RE = {slug: re.compile(rx) for slug, rx in TOPICS.items()}

# Corporate suffixes stripped when deriving a company's headline-matchable
# name from the ticker universe ("ASML Holding N.V." -> "ASML").
_NAME_SUFFIXES = re.compile(
    r"\b(corporation|corp\.?|incorporated|inc\.?|company|co\.?|n\.?v\.?|plc|ag|se|sa|"
    r"s\.p\.a\.?|holdings?|group|ltd\.?|limited|the)\b|\.com", re.IGNORECASE)

# Fund families whose name in a headline says nothing about the specific
# ETF/fund a user holds — matching them would only produce noise.
_GENERIC_ISSUERS = {"vanguard", "ishares", "xtrackers", "amundi", "invesco",
                    "spdr", "lyxor", "wisdomtree", "vaneck", "blackrock"}

# Company-name first tokens that are also ordinary English words ("Home
# Depot" -> "home") — for these, require the two-token phrase instead.
_COMMON_WORDS = {"home", "target", "gap", "best", "first", "general", "american",
                 "united", "national", "international", "global", "standard",
                 "public", "key", "one", "new", "next", "advanced", "micro"}


def _get(url, timeout=20):
    with urlopen(Request(url, headers=NEWS_UA), timeout=timeout) as r:
        return r.read()


def _parse_date(s):
    """RSS dates arrive in three flavors across our feeds: RFC-822
    ('Sat, 11 Jul 2026 12:47:23 GMT'), ISO ('2026-07-11T16:47:00Z') and
    bare 'YYYY-MM-DD HH:MM:SS'. Returns an aware UTC datetime or None."""
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc)
    except (ValueError, TypeError):
        pass
    for fmt in (None, "%Y-%m-%d %H:%M:%S"):
        try:
            d = (dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if fmt is None
                 else dt.datetime.strptime(s, fmt))
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return d.astimezone(dt.timezone.utc)
        except ValueError:
            continue
    return None


def _build_matchers(ticker_list_path):
    """From the published ticker universe, build [(compiled_regex,
    base_symbol)] used to tag headlines. Two kinds of matcher per listing:
    the cleaned company name ('NVIDIA', case-insensitive) and the bare
    symbol itself ('NVDA', case-sensitive — symbols are short and would
    otherwise match ordinary words)."""
    with open(ticker_list_path) as f:
        universe = json.load(f)
    matchers = []
    seen = set()
    for t in universe:
        base = t["symbol"].split(".")[0].split("-")[0].upper()
        name = _NAME_SUFFIXES.sub(" ", t["name"]).strip()
        tokens = name.split()
        term = ""
        if tokens:
            term = (" ".join(tokens[:2]) if tokens[0].lower() in _COMMON_WORDS and len(tokens) >= 2
                    else tokens[0])
        if (term and len(term) >= 3 and term.lower() not in _GENERIC_ISSUERS
                and (term.lower(), base) not in seen):
            matchers.append((re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE), base))
            seen.add((term.lower(), base))
        if len(base) >= 3 and base.isalpha() and ("sym:" + base) not in seen:
            matchers.append((re.compile(r"\b" + base + r"\b"), base))
            seen.add("sym:" + base)
    return matchers


def _tag(title, matchers):
    tickers = sorted({base for rx, base in matchers if rx.search(title)})
    low = title.lower()
    topics = [slug for slug, rx in _TOPIC_RE.items() if rx.search(low)]
    return tickers, topics


def fetch_news(ticker_list_path):
    """Fetch all feeds, tag, dedupe, and return {'generated', 'items'}.
    Each feed failure is reported and skipped — one dead source must not
    take down the whole news build."""
    matchers = _build_matchers(ticker_list_path)
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=MAX_AGE_DAYS)

    items, seen_titles = [], set()
    for source, url in FEEDS:
        try:
            root = ET.fromstring(_get(url))
        except (URLError, HTTPError, TimeoutError, ET.ParseError) as e:
            print(f"  ! news feed failed ({source}): {e}")
            continue
        count = 0
        for it in root.findall(".//item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            pub = _parse_date(it.findtext("pubDate"))
            if not title or not link or not pub or pub < cutoff:
                continue
            if source == "Reuters":  # Google News appends " - Reuters"
                title = re.sub(r"\s+-\s+Reuters.*$", "", title)
            key = re.sub(r"[^a-z0-9]", "", title.lower())[:60]
            if key in seen_titles:
                continue
            seen_titles.add(key)
            tickers, topics = _tag(title, matchers)
            items.append({"title": title, "url": link, "source": source,
                          "published": pub.isoformat(), "tickers": tickers, "topics": topics})
            count += 1
        print(f"  news: {source}: {count} items")

    items.sort(key=lambda x: x["published"], reverse=True)
    return {"generated": now.isoformat(), "items": items[:MAX_ITEMS]}
