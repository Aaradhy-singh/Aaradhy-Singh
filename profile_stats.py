"""Generate self-hosted public repository and language cards for the profile README."""
from __future__ import annotations

import json
import os
import random
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
OUTPUT_STATS = Path(os.environ.get("STATS_OUT_FILE", "profile-stats.svg"))
OUTPUT_LANGUAGES = Path(os.environ.get("LANGUAGES_OUT_FILE", "language-breakdown.svg"))
LANGUAGES_PER_REPOSITORY = 100

QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    followers { totalCount }
    repositories(first: 100, after: $after, privacy: PUBLIC, isFork: false, ownerAffiliations: OWNER) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        stargazerCount
        languages(first: 100, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""


class GitHubAPIError(RuntimeError):
    """Raised when GitHub returns an unusable statistics response."""


def fetch_profile_stats(login: str, token: str) -> dict[str, Any]:
    """Fetch public repository stars, follower count, and language byte totals."""
    if not login.strip():
        raise ValueError("GH_USER must not be empty")
    if not token.strip():
        raise ValueError("GH_TOKEN must not be empty")

    repositories = 0
    stars = 0
    followers = None
    languages: dict[str, dict[str, Any]] = defaultdict(lambda: {"size": 0, "color": None})
    after = None
    seen_cursors: set[str] = set()

    while True:
        payload = json.dumps({
            "query": QUERY,
            "variables": {"login": login, "after": after},
        }).encode("utf-8")
        request = urllib.request.Request(
            GRAPHQL_ENDPOINT,
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "profile-stats-action",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise GitHubAPIError(
                f"GitHub GraphQL returned HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GitHubAPIError(f"Could not reach GitHub GraphQL: {exc}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubAPIError("GitHub GraphQL returned invalid JSON") from exc

        if not isinstance(result, dict):
            raise GitHubAPIError("GitHub GraphQL returned JSON that was not an object")
        errors = result.get("errors", [])
        if errors:
            messages = "; ".join(
                str(error.get("message", "unknown GraphQL error"))
                for error in errors if isinstance(error, dict)
            )
            raise GitHubAPIError(f"GitHub GraphQL error: {messages or 'unknown error'}")

        try:
            user = result["data"]["user"]
            if user is None:
                raise GitHubAPIError(f"GitHub user {login!r} was not found")
            connection = user["repositories"]
            nodes = connection["nodes"]
            page_info = connection["pageInfo"]
            if followers is None:
                followers = user["followers"]["totalCount"]
                repositories = connection["totalCount"]
        except (KeyError, TypeError) as exc:
            raise GitHubAPIError("GitHub response did not include public profile statistics") from exc

        try:
            for repository in nodes:
                stars += repository["stargazerCount"]
                for edge in repository["languages"]["edges"]:
                    language = edge["node"]
                    entry = languages[language["name"]]
                    entry["size"] += edge["size"]
                    if entry["color"] is None and language.get("color"):
                        entry["color"] = language["color"]
        except (KeyError, TypeError) as exc:
            raise GitHubAPIError("GitHub response contained invalid repository language data") from exc

        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor or cursor in seen_cursors:
            raise GitHubAPIError("GitHub repository pagination returned an invalid cursor")
        seen_cursors.add(cursor)
        after = cursor

    return {
        "repositories": repositories,
        "stars": stars,
        "followers": followers or 0,
        "languages": dict(languages),
    }


def demo_profile_stats() -> dict[str, Any]:
    """Return stable sample data for local rendering checks."""
    return {
        "repositories": 18,
        "stars": 42,
        "followers": 27,
        "languages": {
            "TypeScript": {"size": 84000, "color": "#3178C6"},
            "JavaScript": {"size": 51000, "color": "#F1E05A"},
            "Python": {"size": 36000, "color": "#3572A5"},
            "HTML": {"size": 22000, "color": "#E34C26"},
            "CSS": {"size": 19000, "color": "#563D7C"},
        },
    }


def render_profile_stats(stats: dict[str, Any]) -> str:
    """Render public repository count, stars, and followers as a themed SVG."""
    width, height = 920, 190
    metrics = [
        ("PUBLIC REPOSITORIES", stats["repositories"]),
        ("REPOSITORY STARS", stats["stars"]),
        ("FOLLOWERS", stats["followers"]),
    ]
    cards = []
    for index, (label, value) in enumerate(metrics):
        center_x = 166 + index * 294
        cards.append(
            f'<g><rect x="{center_x - 132}" y="82" width="264" height="82" rx="12" '
            f'fill="#15122B" stroke="#302B63"/>'
            f'<text x="{center_x}" y="111" text-anchor="middle" fill="#A78BFA" '
            f'font-size="11" font-weight="600" letter-spacing="1">{escape(label)}</text>'
            f'<text x="{center_x}" y="146" text-anchor="middle" fill="#F8FAFC" '
            f'font-size="27" font-weight="700">{int(value):,}</text></g>'
        )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">Public GitHub repository statistics</title>
  <desc id="desc">Public repositories, stars across those repositories, and followers.</desc>
  <rect width="{width}" height="{height}" rx="16" fill="#0D0221" stroke="#302B63"/>
  <text x="34" y="48" fill="#C4B5FD" font-size="20" font-weight="700">GitHub Snapshot</text>
  {''.join(cards)}
</svg>
'''


def render_language_stats(languages: dict[str, dict[str, Any]], limit: int = 8) -> str:
    """Render the largest language byte totals across public repositories."""
    top = sorted(
        ((name, data) for name, data in languages.items() if int(data.get("size", 0)) > 0),
        key=lambda item: int(item[1]["size"]),
        reverse=True,
    )[:limit]
    width = 920
    row_height = 32
    height = 90 + max(1, len(top)) * row_height
    total = sum(int(data["size"]) for data in languages.values())
    rows = []

    if not top or total <= 0:
        rows.append(
            '<text x="34" y="111" fill="#C4B5FD" font-size="14">'
            'No public repository language data available.</text>'
        )
    else:
        bar_x, bar_width = 278, 490
        for index, (name, data) in enumerate(top):
            y = 82 + index * row_height
            size = int(data["size"])
            percent = size / total * 100
            raw_color = str(data.get("color") or "#8B5CF6")
            color = raw_color if re.fullmatch(r"#[0-9A-Fa-f]{6}", raw_color) else "#8B5CF6"
            rows.append(
                f'<text x="34" y="{y + 17}" fill="#E2E8F0" font-size="13">'
                f'{escape(name)}</text>'
                f'<rect x="{bar_x}" y="{y + 3}" width="{bar_width}" height="14" rx="7" '
                f'fill="#211C3A"/>'
                f'<rect x="{bar_x}" y="{y + 3}" width="{max(2, bar_width * percent / 100):.1f}" '
                f'height="14" rx="7" fill="{color}"/>'
                f'<text x="{bar_x + bar_width + 16}" y="{y + 16}" fill="#C4B5FD" '
                f'font-size="12">{percent:.1f}%</text>'
            )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">Languages in public GitHub repositories</title>
  <desc id="desc">Relative source-code byte totals for each language in public, non-fork repositories.</desc>
  <rect width="{width}" height="{height}" rx="16" fill="#0D0221" stroke="#302B63"/>
  <text x="34" y="39" fill="#C4B5FD" font-size="20" font-weight="700">Languages in Public Repositories</text>
  <text x="34" y="60" fill="#A78BFA" font-size="11">Share of detected source bytes across public, non-fork repositories</text>
  {''.join(rows)}
</svg>
'''


def main() -> None:
    """Generate both profile cards from live public repository data or demo data."""
    if "--demo" in sys.argv:
        stats = demo_profile_stats()
    else:
        login = os.environ.get("GH_USER", "")
        token = os.environ.get("GH_TOKEN", "")
        try:
            stats = fetch_profile_stats(login, token)
        except (GitHubAPIError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc

    OUTPUT_STATS.write_text(render_profile_stats(stats), encoding="utf-8")
    OUTPUT_LANGUAGES.write_text(render_language_stats(stats["languages"]), encoding="utf-8")
    print(
        f"Wrote {OUTPUT_STATS} and {OUTPUT_LANGUAGES} from "
        f"{stats['repositories']} public repositories"
    )


if __name__ == "__main__":
    main()
