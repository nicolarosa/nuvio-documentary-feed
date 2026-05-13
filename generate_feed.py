import os
import sys
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TMDB_DISCOVER_URL = "https://api.themoviedb.org/3/discover/movie"
POSTER_BASE = "https://image.tmdb.org/t/p/w500"
BACKDROP_BASE = "https://image.tmdb.org/t/p/w1280"

MIN_RATING = 7.0
MIN_VOTES = 50
YEARS_BACK = 5
PAGE_DELAY = 0.25


def build_session(token):
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def date_range():
    today = date.today()
    try:
        start = today.replace(year=today.year - YEARS_BACK)
    except ValueError:
        start = today.replace(year=today.year - YEARS_BACK, day=today.day - 1)
    return start.isoformat(), today.isoformat()


def fetch_all_pages(session, start_date, end_date):
    params = {
        "with_genres": "99",
        "vote_average.gte": str(MIN_RATING),
        "vote_count.gte": str(MIN_VOTES),
        "primary_release_date.gte": start_date,
        "primary_release_date.lte": end_date,
        "sort_by": "primary_release_date.desc",
        "language": "en-US",
        "page": 1,
    }

    resp = session.get(TMDB_DISCOVER_URL, params=params)
    resp.raise_for_status()
    data = resp.json()

    total_pages = data["total_pages"]
    total_results = data["total_results"]
    results = list(data["results"])

    print(f"  Page 1/{total_pages} — {len(results)} results (total: {total_results})")

    for page in range(2, total_pages + 1):
        time.sleep(PAGE_DELAY)
        params["page"] = page
        resp = session.get(TMDB_DISCOVER_URL, params=params)
        resp.raise_for_status()
        page_data = resp.json()
        results.extend(page_data["results"])
        print(f"  Page {page}/{total_pages} — {len(page_data['results'])} results")

    return results, total_pages


def transform(raw):
    return {
        "id": raw["id"],
        "title": raw["title"],
        "original_title": raw["original_title"],
        "original_language": raw["original_language"],
        "overview": raw["overview"],
        "release_date": raw["release_date"],
        "vote_average": raw["vote_average"],
        "vote_count": raw["vote_count"],
        "popularity": raw["popularity"],
        "poster_url": f"{POSTER_BASE}{raw['poster_path']}" if raw.get("poster_path") else None,
        "backdrop_url": f"{BACKDROP_BASE}{raw['backdrop_path']}" if raw.get("backdrop_path") else None,
        "genre_ids": raw["genre_ids"],
        "adult": raw["adult"],
        "tmdb_url": f"https://www.themoviedb.org/movie/{raw['id']}",
    }


def main():
    token = os.environ.get("TMDB_API_TOKEN")
    if not token:
        print("Error: TMDB_API_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    session = build_session(token)
    start_date, end_date = date_range()

    print(f"Fetching documentaries rated >= {MIN_RATING} with >= {MIN_VOTES} votes")
    print(f"Date range: {start_date} to {end_date}")

    raw_results, pages_fetched = fetch_all_pages(session, start_date, end_date)
    documentaries = [transform(r) for r in raw_results]

    feed = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generator": "nuvio-documentary-feed",
            "version": "1.0.0",
            "parameters": {
                "min_rating": MIN_RATING,
                "min_votes": MIN_VOTES,
                "years_back": YEARS_BACK,
                "genre_id": 99,
                "sort_by": "primary_release_date.desc",
            },
            "total_results": len(documentaries),
            "total_pages_fetched": pages_fetched,
            "tmdb_attribution": "This product uses the TMDB API but is not endorsed or certified by TMDB.",
        },
        "documentaries": documentaries,
    }

    output_path = Path(__file__).resolve().parent / "docs" / "documentaries.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(feed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\nDone — {len(documentaries)} documentaries written to {output_path}")


if __name__ == "__main__":
    main()
