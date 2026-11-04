"""Generate the profile contribution chart as a self-contained SVG.

The chart shows 52 weekly totals from the GitHub GraphQL contribution calendar.
Set GH_USER and GH_TOKEN for live data, or pass --demo for synthetic data.
"""
from __future__ import annotations

import json
import os
import random
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Sequence
from xml.sax.saxutils import escape

WEEKS = 52
OUTPUT_FILE = os.environ.get("OUT_FILE", "activity-graph.svg")
GRAPHQL_ENDPOINT = "https://api.github.com/graphql"

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


class GitHubAPIError(RuntimeError):
    """Raised when GitHub returns an unusable contribution response."""


def fetch_weekly_totals(user: str, token: str, today: date | None = None) -> list[tuple[date, int]]:
    """Fetch daily contribution counts and aggregate them into 52 full weeks."""
    if not user.strip():
        raise ValueError("GH_USER must not be empty")
    if not token.strip():
        raise ValueError("GH_TOKEN must not be empty")

    current_day = today or datetime.now(timezone.utc).date()
    first_day = current_day - timedelta(days=WEEKS * 7)
    last_day = current_day - timedelta(days=1)
    payload = json.dumps({
        "query": QUERY,
        "variables": {
            "login": user,
            "from": f"{first_day.isoformat()}T00:00:00Z",
            "to": f"{current_day.isoformat()}T00:00:00Z",
        },
    }).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_ENDPOINT,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "activity-graph-action",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise GitHubAPIError(f"GitHub GraphQL returned HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GitHubAPIError(f"Could not reach GitHub GraphQL: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitHubAPIError("GitHub GraphQL returned invalid JSON") from exc

    errors = result.get("errors")
    if errors:
        messages = "; ".join(
            str(error.get("message", "unknown GraphQL error"))
            for error in errors if isinstance(error, dict)
        )
        raise GitHubAPIError(f"GitHub GraphQL error: {messages or 'unknown error'}")

    try:
        user_data = result["data"]["user"]
        if user_data is None:
            raise GitHubAPIError(f"GitHub user {user!r} was not found")
        contribution_weeks = user_data["contributionsCollection"]["contributionCalendar"]["weeks"]
    except (KeyError, TypeError) as exc:
        raise GitHubAPIError("GitHub GraphQL response did not include a contribution calendar") from exc

    counts_by_date: dict[date, int] = {}
    try:
        for week in contribution_weeks:
            for day_data in week["contributionDays"]:
                day = date.fromisoformat(day_data["date"])
                count = day_data["contributionCount"]
                if type(count) is not int or count < 0:
                    raise ValueError("contributionCount must be a non-negative integer")
                counts_by_date[day] = count
    except (KeyError, TypeError, ValueError) as exc:
        raise GitHubAPIError(f"GitHub contribution calendar contains invalid day data: {exc}") from exc

    weekly_totals = []
    for week_index in range(WEEKS):
        week_start = first_day + timedelta(days=week_index * 7)
        week_total = sum(
            counts_by_date.get(week_start + timedelta(days=offset), 0)
            for offset in range(7)
            if week_start + timedelta(days=offset) <= last_day
        )
        weekly_totals.append((week_start, week_total))
    return weekly_totals


def demo_weekly_totals(today: date | None = None) -> list[tuple[date, int]]:
    """Return stable synthetic data for local rendering checks."""
    current_day = today or date.today()
    first_day = current_day - timedelta(days=WEEKS * 7)
    random.seed(7)
    return [
        (first_day + timedelta(days=index * 7), random.choice([0, 0, 1, 2, 3, 5, 8, 12]))
        for index in range(WEEKS)
    ]


def render(weekly_totals: Sequence[tuple[date, int]]) -> str:
    """Render weekly contribution totals using the profile's existing SVG style."""
    if len(weekly_totals) != WEEKS:
        raise ValueError(f"Expected {WEEKS} weekly totals, received {len(weekly_totals)}")
    if any(type(count) is not int or count < 0 for _, count in weekly_totals):
        raise ValueError("Weekly contribution totals must be non-negative integers")

    width, height = 900, 320
    left, right, top, bottom = 56, 28, 70, 52
    plot_width, plot_height = width - left - right, height - top - bottom
    counts = [count for _, count in weekly_totals]
    y_max = max(5, ((max(counts) + 4) // 5) * 5)
    total = sum(counts)

    def x(index: int) -> float:
        return left + plot_width * index / (WEEKS - 1)

    def y(value: float) -> float:
        return top + plot_height * (1 - value / y_max)

    points = [(x(index), y(count)) for index, (_, count) in enumerate(weekly_totals)]
    line = " ".join(f"{point_x:.1f},{point_y:.1f}" for point_x, point_y in points)
    area = f"{left},{top + plot_height} {line} {left + plot_width},{top + plot_height}"

    grid = []
    for step in range(6):
        value = y_max * step / 5
        grid_y = y(value)
        grid.append(
            f'<line x1="{left}" y1="{grid_y:.1f}" x2="{left + plot_width}" y2="{grid_y:.1f}" '
            f'stroke="#A78BFA" stroke-opacity="0.15"/>'
            f'<text x="{left - 10}" y="{grid_y + 4:.1f}" text-anchor="end" '
            f'fill="#C4B5FD" font-size="11">{int(value)}</text>'
        )

    month_labels = []
    last_month = None
    for index, (week_start, _) in enumerate(weekly_totals):
        if week_start.month != last_month:
            month_labels.append(
                f'<text x="{x(index):.1f}" y="{top + plot_height + 22}" text-anchor="middle" '
                f'fill="#C4B5FD" font-size="11">{week_start.strftime("%b")}</text>'
            )
            last_month = week_start.month

    dots = "".join(
        f'<circle cx="{point_x:.1f}" cy="{point_y:.1f}" r="3" fill="#C4B5FD" '
        f'stroke="#0D0221" stroke-width="1.5"><title>Week of {escape(week_start.isoformat())}: '
        f'{count}</title></circle>'
        for (point_x, point_y), (week_start, count) in zip(points, weekly_totals)
    )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="'Segoe UI', Helvetica, Arial, sans-serif">
  <defs>
    <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#8B5CF6" stop-opacity="0.45"/>
      <stop offset="100%" stop-color="#8B5CF6" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <rect width="{width}" height="{height}" rx="12" fill="#0D0221"/>
  <text x="{left}" y="34" fill="#C4B5FD" font-size="18" font-weight="600">Contribution Activity</text>
  <text x="{left}" y="54" fill="#A78BFA" font-size="12">Last {WEEKS} weeks, {total} contributions, shown per week</text>
  {''.join(grid)}
  <polygon points="{area}" fill="url(#fill)"/>
  <polyline points="{line}" fill="none" stroke="#8B5CF6" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
  {dots}
  {''.join(month_labels)}
</svg>
'''


def main() -> None:
    """Generate the SVG from live GitHub data or deterministic demo data."""
    if "--demo" in sys.argv:
        weekly_totals = demo_weekly_totals()
    else:
        user = os.environ.get("GH_USER", "")
        token = os.environ.get("GH_TOKEN", "")
        if not user:
            raise SystemExit("Missing required environment variable: GH_USER")
        if not token:
            raise SystemExit("Missing required environment variable: GH_TOKEN")
        try:
            weekly_totals = fetch_weekly_totals(user, token)
        except (GitHubAPIError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc

    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="\n") as svg_file:
        svg_file.write(render(weekly_totals))
    print(f"Wrote {OUTPUT_FILE} ({sum(count for _, count in weekly_totals)} contributions over {WEEKS} weeks)")


if __name__ == "__main__":
    main()
