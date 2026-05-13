import os
import sys
import json
import time
from datetime import date
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TMDB_BASE = "https://api.themoviedb.org/3"
POSTER_BASE = "https://image.tmdb.org/t/p/w500"
BACKDROP_BASE = "https://image.tmdb.org/t/p/original"

MIN_RATING = 7.0
MIN_VOTES = 50
YEARS_BACK = 5
REQUEST_DELAY = 0.25

ADDON_ID = "com.nuvio.documentary-feed"
ADDON_VERSION = "1.0.0"
ADDON_NAME = "Top Documentaries"
ADDON_DESCRIPTION = (
    "Highest-rated documentaries from the last 5 years (TMDB rating >= 7.0, "
    "50+ votes), sorted newest to oldest. Updated daily."
)
CATALOG_ID = "top-docs-5y"
CATALOG_NAME = "Top Documentaries (5y)"


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


def fetch_genre_map(session):
    resp = session.get(f"{TMDB_BASE}/genre/movie/list", params={"language": "en-US"})
    resp.raise_for_status()
    return {g["id"]: g["name"] for g in resp.json()["genres"]}


def fetch_documentaries(session, start_date, end_date):
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
    resp = session.get(f"{TMDB_BASE}/discover/movie", params=params)
    resp.raise_for_status()
    data = resp.json()
    total_pages = data["total_pages"]
    results = list(data["results"])
    print(f"  Page 1/{total_pages} — {len(results)} results (total: {data['total_results']})")

    for page in range(2, total_pages + 1):
        time.sleep(REQUEST_DELAY)
        params["page"] = page
        resp = session.get(f"{TMDB_BASE}/discover/movie", params=params)
        resp.raise_for_status()
        page_data = resp.json()
        results.extend(page_data["results"])
        print(f"  Page {page}/{total_pages} — {len(page_data['results'])} results")

    return results


def fetch_imdb_id(session, tmdb_id):
    try:
        resp = session.get(f"{TMDB_BASE}/movie/{tmdb_id}/external_ids")
        resp.raise_for_status()
        return resp.json().get("imdb_id") or None
    except requests.HTTPError as e:
        print(f"  ! IMDB lookup failed for tmdb:{tmdb_id} — {e}")
        return None


def to_stremio_meta(raw, imdb_id, genre_map):
    return {
        "id": imdb_id,
        "type": "movie",
        "name": raw["title"],
        "poster": f"{POSTER_BASE}{raw['poster_path']}" if raw.get("poster_path") else None,
        "posterShape": "poster",
        "background": f"{BACKDROP_BASE}{raw['backdrop_path']}" if raw.get("backdrop_path") else None,
        "description": raw.get("overview") or None,
        "releaseInfo": raw["release_date"][:4] if raw.get("release_date") else None,
        "imdbRating": f"{raw['vote_average']:.1f}",
        "genres": [genre_map[gid] for gid in raw.get("genre_ids", []) if gid in genre_map],
    }


def write_manifest(output_dir):
    manifest = {
        "id": ADDON_ID,
        "version": ADDON_VERSION,
        "name": ADDON_NAME,
        "description": ADDON_DESCRIPTION,
        "resources": ["catalog"],
        "types": ["movie"],
        "idPrefixes": ["tt"],
        "catalogs": [
            {
                "type": "movie",
                "id": CATALOG_ID,
                "name": CATALOG_NAME,
            }
        ],
        "behaviorHints": {
            "configurable": False,
            "configurationRequired": False,
        },
    }
    path = output_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def write_catalog(output_dir, metas):
    catalog_dir = output_dir / "catalog" / "movie"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    path = catalog_dir / f"{CATALOG_ID}.json"
    path.write_text(
        json.dumps({"metas": metas}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def main():
    token = os.environ.get("TMDB_API_TOKEN")
    if not token:
        print("Error: TMDB_API_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    session = build_session(token)
    start_date, end_date = date_range()

    print("Fetching TMDB genre map...")
    genre_map = fetch_genre_map(session)

    print(f"Fetching documentaries (rating >= {MIN_RATING}, votes >= {MIN_VOTES})")
    print(f"Date range: {start_date} to {end_date}")
    raw_results = fetch_documentaries(session, start_date, end_date)

    print(f"\nResolving IMDB IDs for {len(raw_results)} documentaries...")
    metas = []
    skipped = 0
    for i, raw in enumerate(raw_results, 1):
        time.sleep(REQUEST_DELAY)
        imdb_id = fetch_imdb_id(session, raw["id"])
        if imdb_id:
            metas.append(to_stremio_meta(raw, imdb_id, genre_map))
        else:
            skipped += 1
        if i % 20 == 0 or i == len(raw_results):
            print(f"  {i}/{len(raw_results)} resolved (skipped {skipped})")

    output_dir = Path(__file__).resolve().parent / "docs"
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = write_manifest(output_dir)
    catalog_path = write_catalog(output_dir, metas)

    legacy = output_dir / "documentaries.json"
    if legacy.exists():
        legacy.unlink()
        print(f"  Removed legacy {legacy}")

    print(f"\nDone — Stremio-compliant addon written:")
    print(f"  {manifest_path}")
    print(f"  {catalog_path}")
    print(f"  Catalog entries: {len(metas)} (skipped {skipped} without IMDB IDs)")


if __name__ == "__main__":
    main()
