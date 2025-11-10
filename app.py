import os
import logging
from datetime import datetime
from typing import List, Dict, Any

import requests
from requests.exceptions import RequestException
from flask import Flask, render_template, abort
from dotenv import load_dotenv
from newspaper import Article


# --------------------------
# Load environment variables
# --------------------------
load_dotenv()

NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
NEWSAPI_ENDPOINT = os.getenv("NEWSAPI_ENDPOINT", "https://newsapi.org/v2/top-headlines")
COUNTRY = os.getenv("COUNTRY", "us")
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "10"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "6"))

# --------------------------
# Flask app and logging
# --------------------------
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.context_processor
def inject_datetime():
    return {'datetime': datetime}


# ✅ Make datetime accessible in templates (Fix for your Jinja2 error)
app.jinja_env.globals['datetime'] = datetime

# --------------------------
# Caching
# --------------------------
cached_articles: List[Dict[str, Any]] = []

# Local folder for cached images
LOCAL_IMG_FOLDER = os.path.join("static", "images", "news")
os.makedirs(LOCAL_IMG_FOLDER, exist_ok=True)

# Placeholder image path
PLACEHOLDER_IMAGE = "/static/images/placeholder.jpg"

# --------------------------
# Utility functions
# --------------------------
def parse_datetime(val: str):
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.strptime(val, "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None


def download_image(url: str, article_id: int) -> str:
    """Download image locally and return relative path"""
    if not url:
        return PLACEHOLDER_IMAGE

    ext = url.split(".")[-1].split("?")[0]
    if ext.lower() not in ["jpg", "jpeg", "png", "webp"]:
        ext = "jpg"
    local_filename = os.path.join(LOCAL_IMG_FOLDER, f"{article_id}.{ext}")

    if os.path.exists(local_filename):
        return "/" + local_filename.replace("\\", "/")

    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            with open(local_filename, "wb") as f:
                f.write(resp.content)
            return "/" + local_filename.replace("\\", "/")
    except Exception as e:
        logger.info("Failed to download image: %s", e)
    return PLACEHOLDER_IMAGE


def fetch_headlines(country=COUNTRY, page_size=PAGE_SIZE) -> List[Dict[str, Any]]:
    """Fetch headlines from NewsAPI and keep only articles with images"""
    if not NEWSAPI_KEY:
        logger.warning("NEWSAPI_KEY not set in .env")
        return []

    params = {"apiKey": NEWSAPI_KEY, "country": country, "pageSize": page_size * 2}  # fetch extra
    try:
        resp = requests.get(NEWSAPI_ENDPOINT, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch headlines: %s", e)
        data = {}

    articles_list = data.get("articles", [])
    out = []

    for i, a in enumerate(articles_list, start=1):
        article = {
            "id": i,
            "title": a.get("title") or "No title",
            "description": a.get("description") or "",
            "content": a.get("content") or "",
            "source_name": a.get("source", {}).get("name") or "",
            "source_url": a.get("url") or "#",
            "published_at": parse_datetime(a.get("publishedAt")),
            "full_text": None,
        }
        # Download image or use placeholder
        article["image_url"] = download_image(a.get("urlToImage"), i) if a.get("urlToImage") else PLACEHOLDER_IMAGE
        out.append(article)
        if len(out) >= page_size:
            break

    if not out:
        out = [{
            "id": 1,
            "title": "No articles available right now",
            "description": "Please check back later.",
            "content": "",
            "source_name": "",
            "source_url": "#",
            "image_url": PLACEHOLDER_IMAGE,
            "published_at": None,
            "full_text": ""
        }]
    return out


def enrich_article_with_newspaper(article: Dict[str, Any]) -> None:
    """Fetch full article text on-demand"""
    url = article.get("source_url")
    if not url or url == "#":
        return
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
        if resp.status_code != 200:
            return
    except Exception:
        return

    try:
        art = Article(url, language="en")
        art.set_html(resp.text)
        art.parse()
    except Exception:
        return

    article["full_text"] = getattr(art, "text", "") or article.get("content") or article.get("description")


# --------------------------
# Flask filters
# --------------------------
@app.template_filter('datetimeformat')
def datetimeformat(value, fmt='%b %d, %Y %I:%M %p'):
    if not value:
        return "Unknown"
    try:
        return value.strftime(fmt)
    except Exception:
        return str(value)


# --------------------------
# Routes
# --------------------------
@app.route('/')
def index():
    global cached_articles
    if not cached_articles:
        cached_articles = fetch_headlines()
    return render_template("index.html", articles=cached_articles)


@app.route('/article/<int:article_id>')
def article(article_id: int):
    art = next((x for x in cached_articles if x["id"] == article_id), None)
    if not art:
        abort(404)
    if not art.get("full_text"):
        enrich_article_with_newspaper(art)
    return render_template("article.html", article=art)


# --------------------------
# Main
# --------------------------
if __name__ == '__main__':
    app.run(debug=True, threaded=True, host="127.0.0.1", port=5000)
