"""Generate activity-graph.svg: a 31-day contribution line chart.

Reads the contribution calendar from the GitHub GraphQL API using
GH_TOKEN and GH_USER, then writes a self-contained SVG. No third-party
service is involved, so the image cannot go down.

Run with --demo to render with synthetic data (no token needed).
"""
import json
import os
import random
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

DAYS = 31
OUT = os.environ.get("OUT_FILE", "activity-graph.svg")

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


def fetch_days(user, token):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=DAYS + 7)
    payload = json.dumps({
        "query": QUERY,
        "variables": {
            "login": user,
            "from": start.strftime("%Y-%m-%dT00:00:00Z"),
            "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "activity-graph-action",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if "errors" in data or not data.get("data", {}).get("user"):
        raise SystemExit(f"GraphQL error: {data}")
    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    by_date = {}
    for w in weeks:
        for d in w["contributionDays"]:
            by_date[d["date"]] = d["contributionCount"]
    today = now.date()
    out = []
    for i in range(DAYS - 1, -1, -1):
        day = today - timedelta(days=i)
        out.append((day, by_date.get(day.isoformat(), 0)))
    return out


def demo_days():
    random.seed(7)
    today = date.today()
    return [(today - timedelta(days=i), random.choice([0, 0, 1, 2, 3, 5, 8, 12]))
            for i in range(DAYS - 1, -1, -1)]


def render(days):
    W, H = 900, 320
    left, right, top, bottom = 56, 28, 70, 52
    pw, ph = W - left - right, H - top - bottom
    counts = [c for _, c in days]
    ymax = max(5, max(counts))
    # round the axis maximum up to a multiple of 5
    ymax = ((ymax + 4) // 5) * 5
    total = sum(counts)

    def x(i):
        return left + pw * i / (len(days) - 1)

    def y(v):
        return top + ph * (1 - v / ymax)

    pts = [(x(i), y(c)) for i, (_, c) in enumerate(days)]
    line = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
    area = f"{left},{top + ph} " + line + f" {left + pw},{top + ph}"

    grid = []
    for step in range(0, 6):
        v = ymax * step / 5
        gy = y(v)
        grid.append(
            f'<line x1="{left}" y1="{gy:.1f}" x2="{left + pw}" y2="{gy:.1f}" '
            f'stroke="#A78BFA" stroke-opacity="0.15"/>'
            f'<text x="{left - 10}" y="{gy + 4:.1f}" text-anchor="end" '
            f'fill="#C4B5FD" font-size="11">{int(v)}</text>'
        )

    labels = []
    for i, (d, _) in enumerate(days):
        if i % 5 == 0 or i == len(days) - 1:
            labels.append(
                f'<text x="{x(i):.1f}" y="{top + ph + 22}" text-anchor="middle" '
                f'fill="#C4B5FD" font-size="11">{d.strftime("%b %d").replace(" 0", " ")}</text>'
            )

    dots = "".join(
        f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="#C4B5FD" stroke="#0D0221" stroke-width="1.5">'
        f'<title>{d.isoformat()}: {c}</title></circle>'
        for (px, py), (d, c) in zip(pts, days)
    )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="'Segoe UI', Helvetica, Arial, sans-serif">
  <defs>
    <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#8B5CF6" stop-opacity="0.45"/>
      <stop offset="100%" stop-color="#8B5CF6" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <rect width="{W}" height="{H}" rx="12" fill="#0D0221"/>
  <text x="{left}" y="34" fill="#C4B5FD" font-size="18" font-weight="600">Contribution Activity</text>
  <text x="{left}" y="54" fill="#A78BFA" font-size="12">Last {DAYS} days, {total} contributions</text>
  {''.join(grid)}
  <polygon points="{area}" fill="url(#fill)"/>
  <polyline points="{line}" fill="none" stroke="#8B5CF6" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
  {dots}
  {''.join(labels)}
</svg>
'''


def main():
    if "--demo" in sys.argv:
        days = demo_days()
    else:
        user = os.environ["GH_USER"]
        token = os.environ["GH_TOKEN"]
        days = fetch_days(user, token)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(render(days))
    print(f"wrote {OUT} ({sum(c for _, c in days)} contributions over {DAYS} days)")


if __name__ == "__main__":
    main()
