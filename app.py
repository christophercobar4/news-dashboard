import os
import json
import requests
from datetime import date, datetime, timedelta
from flask import Flask, render_template
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

API_KEY = os.getenv("WORLD_NEWS_API_KEY")
CACHE_FILE = "news_cache.json"
BASE_URL = "https://api.worldnewsapi.com"

# Trusted, unbiased news sources (via techworm.net/2021/12/unbiased-news-sources)
# Maps canonical domain → display name
TRUSTED_SOURCE_NAMES = {
    "apnews.com": "Associated Press",
    "reuters.com": "Reuters",
    "bbc.com": "BBC News",
    "wsj.com": "The Wall Street Journal",
    "bloomberg.com": "Bloomberg",
    "nytimes.com": "The New York Times",
    "c-span.org": "C-SPAN",
    "npr.org": "NPR",
    "forbes.com": "Forbes",
    "nbcnews.com": "NBC News",
    "sciencenews.org" : "Science News",
    "newscientist.com" : "New Scientist",
    "scientificamerican.com" : "Scientific American",
    "quantamagazine.org" : "Quanta Magazine",
    "the-scientist.com" : "The Scientist",
    "bbc.com/news/science_and_environment" : "BBC Science",
    "theguardian.com/science" : "The Guardian Science",
    "npr.org/sections/science/" : "NPR Science",
    "arstechnica.com/science/" : "Ars Technica Science",
    "nasa.gov/news/" : "NASA News",
    "statnews.com" : "STAT News",
    "physicstoday.scitation.org" : "Physics Today",
    "eurekalert.org" : "EurekAlert!",   
    "phys.org" : "Phys.org",
}

TRUSTED_SOURCES = ",".join(
    f"https://www.{domain}" if domain not in ("apnews.com",) else f"https://{domain}"
    for domain in TRUSTED_SOURCE_NAMES
)

# Categories to show, in display order
CATEGORIES = [
    ("politics", "US Politics", "🏛️"),
    ("sports", "Sports", "🏆"),
    ("business", "Business", "💼"),
    ("technology", "Technology", "💻"),
    ("entertainment", "Entertainment", "🎬"),
    ("health", "Health", "🩺"),
    ("science", "Science", "🔬"),
]


def load_cache():
    """Load the cached news data from disk."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_cache(data):
    """Persist news data to disk with today's date stamp."""
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)


def is_cache_fresh(cache):
    """Return True if the cache was populated today."""
    cached_date = cache.get("date")
    return cached_date == str(date.today())


def _publisher_from_url(url):
    """Return a human-readable publisher name derived from the article URL."""
    # Strip scheme and www., e.g. "https://www.nbcnews.com/..." -> "nbcnews.com"
    domain = url.split("//")[-1].split("/")[0].lstrip("www.")
    # Walk from most-specific to least-specific subdomain match
    for key, name in TRUSTED_SOURCE_NAMES.items():
        if domain == key or domain.endswith("." + key):
            return name
    return domain or None


def _article_shape(article, extra=None):
    """Normalize a raw API article dict into the shape used by templates."""
    url = article.get("url", "#")
    shape = {
        "title": article.get("title", ""),
        "url": url,
        "image": article.get("image"),
        "summary": article.get("summary") or article.get("text", "")[:200],
        "publish_date": article.get("publish_date", ""),
        "authors": article.get("authors", []),
        "publisher": _publisher_from_url(url),
    }
    if extra:
        shape.update(extra)
    return shape


def fetch_top_us_headlines():
    """Fetch the top 5 US headlines from the Top News endpoint.

    The /top-news endpoint does not support a news-sources filter, so we
    request a larger batch and pick the first article per cluster whose URL
    belongs to one of the trusted domains.
    """
    trusted_domains = {
        s.rstrip("/").split("//")[-1] for s in TRUSTED_SOURCES.split(",")
    }

    url = f"{BASE_URL}/top-news"
    params = {
        "source-country": "us",
        "language": "en",
        "api-key": API_KEY,
    }
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    headlines = []
    for cluster in data.get("top_news", []):
        for article in cluster.get("news", []):
            article_domain = article.get("url", "").split("//")[-1].split("/")[0]
            if article_domain in trusted_domains:
                headlines.append(_article_shape(article))
                break
        if len(headlines) >= 5:
            break

    return headlines


def fetch_top_international_headlines():
    """Fetch the top 5 international (non-US) headlines via Search News."""
    today = str(date.today())
    url = f"{BASE_URL}/search-news"
    params = {
        "language": "en",
        "news-sources": TRUSTED_SOURCES,
        "not-source-countries": "us",
        "sort": "publish-time",
        "sort-direction": "DESC",
        "earliest-publish-date": f"{today} 00:00:00",
        "number": 5,
        "api-key": API_KEY,
    }
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    return [
        _article_shape(a, {"source_country": a.get("source_country", "")})
        for a in data.get("news", [])[:5]
    ]


def fetch_positive_headlines():
    """Fetch the top 5 most positive US English headlines.

    The API does not support sorting by sentiment directly, so we request a
    larger batch filtered by min-sentiment and then sort client-side by score.
    A 2-day rolling window ensures results are available early in the morning.
    """
    yesterday = str(date.today() - timedelta(days=1))
    url = f"{BASE_URL}/search-news"
    params = {
        "language": "en",
        "news-sources": TRUSTED_SOURCES,
        "source-countries": "us",
        "min-sentiment": 0.5,
        "earliest-publish-date": f"{yesterday} 00:00:00",
        "number": 20,
        "api-key": API_KEY,
    }
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    articles = data.get("news", [])
    articles.sort(key=lambda a: a.get("sentiment", 0), reverse=True)

    return [
        _article_shape(a, {"sentiment": a.get("sentiment", 0)}) for a in articles[:5]
    ]


def fetch_category_headlines(category):
    """Fetch the top 5 US headlines for a given category."""
    today = str(date.today())
    url = f"{BASE_URL}/search-news"
    params = {
        "language": "en",
        "news-sources": TRUSTED_SOURCES,
        "categories": category,
        "sort": "publish-time",
        "sort-direction": "DESC",
        "earliest-publish-date": f"{today} 00:00:00",
        "number": 5,
        "api-key": API_KEY,
    }
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    return [_article_shape(a) for a in data.get("news", [])[:5]]


def get_news():
    """
    Return all headline data, using a daily server-side cache so the
    World News API is only called once per calendar day.
    """
    cache = load_cache()

    if is_cache_fresh(cache):
        return (
            cache["us_headlines"],
            cache["international_headlines"],
            cache["positive_headlines"],
            cache["category_headlines"],
        )

    us = fetch_top_us_headlines()
    intl = fetch_top_international_headlines()
    positive = fetch_positive_headlines()

    category_headlines = {}
    for slug, _label, _icon in CATEGORIES:
        category_headlines[slug] = fetch_category_headlines(slug)

    save_cache(
        {
            "date": str(date.today()),
            "us_headlines": us,
            "international_headlines": intl,
            "positive_headlines": positive,
            "category_headlines": category_headlines,
        }
    )

    return us, intl, positive, category_headlines


@app.route("/")
def index():
    error = None
    us_headlines = []
    international_headlines = []
    positive_headlines = []
    category_headlines = {}

    try:
        (
            us_headlines,
            international_headlines,
            positive_headlines,
            category_headlines,
        ) = get_news()
    except requests.HTTPError as exc:
        error = f"API error: {exc.response.status_code} – {exc.response.text}"
    except requests.RequestException as exc:
        error = f"Network error: {exc}"

    return render_template(
        "index.html",
        us_headlines=us_headlines,
        international_headlines=international_headlines,
        positive_headlines=positive_headlines,
        category_headlines=category_headlines,
        categories=CATEGORIES,
        today=datetime.now().strftime("%B %d, %Y"),
        error=error,
    )


if __name__ == "__main__":
    app.run(debug=True)
