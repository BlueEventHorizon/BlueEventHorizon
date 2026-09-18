#!/usr/bin/env python3
"""GitHubの公開データからプロフィール用SVGを生成する。"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any


API_ROOT = "https://api.github.com"
GRAPHQL_ROOT = f"{API_ROOT}/graphql"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
PROFILE_TAGLINE = "iOS Engineer · Swift · Dart / Flutter · Science Fiction"

LANGUAGE_COLORS = {
    "Swift": "#f05138",
    "Dart": "#00b4ab",
    "Python": "#3572a5",
    "TypeScript": "#3178c6",
    "JavaScript": "#f1e05a",
    "Kotlin": "#a97bff",
    "Go": "#00add8",
    "Ruby": "#701516",
    "Shell": "#89e051",
    "HTML": "#e34c26",
    "CSS": "#563d7c",
    "C++": "#f34b7d",
    "C": "#555555",
}


@dataclass(frozen=True)
class ProfileStats:
    public_repos: int
    original_projects: int
    stars_earned: int
    forks_earned: int
    followers: int
    years_on_github: int
    repository_languages: tuple[tuple[str, int], ...]
    code_languages: tuple[tuple[str, int], ...]
    contribution_months: tuple[tuple[str, int], ...]
    contributions_last_year: int
    lifetime_contributions: int
    total_commits: int
    total_pull_requests: int
    total_issues: int
    contributed_repositories: int
    recent_commit_hours: tuple[int, ...]
    recent_commit_count: int


def _request_json(
    url: str, token: str, *, empty_on_status: tuple[int, ...] = ()
) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "BlueEventHorizon-profile-generator",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in empty_on_status:
            return []
        raise RuntimeError(
            f"GitHub API request failed: HTTP {error.code} ({error.reason})"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"GitHub API request failed: {error.reason}") from error


def _graphql_json(query: str, variables: dict[str, Any], token: str) -> dict[str, Any]:
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_ROOT,
        data=body,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "BlueEventHorizon-profile-generator",
            "X-GitHub-Api-Version": "2026-03-10",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"GitHub GraphQL request failed: HTTP {error.code} ({error.reason})"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"GitHub GraphQL request failed: {error.reason}") from error

    if payload.get("errors"):
        messages = "; ".join(
            str(error.get("message", "unknown error")) for error in payload["errors"]
        )
        raise RuntimeError(f"GitHub GraphQL request failed: {messages}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("GitHub GraphQL returned an unexpected payload")
    return data


def _fetch_language_bytes(
    repositories: list[dict[str, Any]], token: str
) -> dict[str, int]:
    totals: dict[str, int] = {}
    for repository in repositories:
        full_name = str(repository.get("full_name", ""))
        parts = full_name.split("/", 1)
        if len(parts) != 2:
            raise RuntimeError(f"Repository has an invalid full_name: {full_name!r}")
        owner, name = (urllib.parse.quote(part, safe="") for part in parts)
        languages = _request_json(
            f"{API_ROOT}/repos/{owner}/{name}/languages", token
        )
        if not isinstance(languages, dict):
            raise RuntimeError(f"Languages API returned invalid data for {full_name}")
        for language, byte_count in languages.items():
            totals[str(language)] = totals.get(str(language), 0) + int(byte_count)
    return totals


def _fetch_contributions(
    username: str, created_year: int, today: date, token: str
) -> dict[str, Any]:
    recent_from = today - timedelta(days=364)
    annual_fields: list[str] = []
    for year in range(max(created_year, 2008), today.year + 1):
        period_end = today if year == today.year else date(year, 12, 31)
        annual_fields.append(
            f"""
      y{year}: contributionsCollection(
        from: \"{year}-01-01T00:00:00Z\"
        to: \"{period_end.isoformat()}T23:59:59Z\"
      ) {{
        contributionCalendar {{ totalContributions }}
        totalCommitContributions
        totalPullRequestContributions
        totalIssueContributions
        commitContributionsByRepository(maxRepositories: 100) {{
          repository {{ nameWithOwner }}
        }}
      }}"""
        )

    query = f"""
query ProfileContributions($login: String!) {{
  user(login: $login) {{
    recent: contributionsCollection(
      from: \"{recent_from.isoformat()}T00:00:00Z\"
      to: \"{today.isoformat()}T23:59:59Z\"
    ) {{
      contributionCalendar {{
        totalContributions
        weeks {{ contributionDays {{ date contributionCount }} }}
      }}
      commitContributionsByRepository(maxRepositories: 100) {{
        repository {{ nameWithOwner isPrivate }}
      }}
    }}
    {''.join(annual_fields)}
  }}
}}
"""
    response = _graphql_json(query, {"login": username}, token)
    user_data = response.get("user")
    if not isinstance(user_data, dict):
        raise RuntimeError(f"GitHub user not found: {username}")

    recent = user_data["recent"]
    calendar = recent["contributionCalendar"]
    days = [
        day
        for week in calendar["weeks"]
        for day in week["contributionDays"]
        if recent_from.isoformat() <= day["date"] <= today.isoformat()
    ]
    recent_repositories = sorted(
        {
            item["repository"]["nameWithOwner"]
            for item in recent["commitContributionsByRepository"]
            if not item["repository"]["isPrivate"]
        }
    )

    lifetime_contributions = 0
    total_commits = 0
    total_pull_requests = 0
    total_issues = 0
    contributed_repositories: set[str] = set()
    for year in range(max(created_year, 2008), today.year + 1):
        annual = user_data[f"y{year}"]
        lifetime_contributions += int(
            annual["contributionCalendar"]["totalContributions"]
        )
        total_commits += int(annual["totalCommitContributions"])
        total_pull_requests += int(annual["totalPullRequestContributions"])
        total_issues += int(annual["totalIssueContributions"])
        contributed_repositories.update(
            item["repository"]["nameWithOwner"]
            for item in annual["commitContributionsByRepository"]
        )

    return {
        "days": days,
        "last_year_total": int(calendar["totalContributions"]),
        "lifetime_total": lifetime_contributions,
        "total_commits": total_commits,
        "total_pull_requests": total_pull_requests,
        "total_issues": total_issues,
        "contributed_repositories": len(contributed_repositories),
        "recent_repositories": recent_repositories,
        "recent_from": recent_from.isoformat(),
    }


def _fetch_recent_commit_dates(
    repositories: list[str], username: str, since: str, token: str
) -> list[str]:
    dates: list[str] = []
    query = urllib.parse.urlencode(
        {
            "author": username,
            "since": f"{since}T00:00:00Z",
            "per_page": 100,
        }
    )
    for full_name in repositories:
        parts = full_name.split("/", 1)
        if len(parts) != 2:
            continue
        owner, name = (urllib.parse.quote(part, safe="") for part in parts)
        for page in range(1, 21):
            commits = _request_json(
                f"{API_ROOT}/repos/{owner}/{name}/commits?{query}&page={page}",
                token,
                empty_on_status=(409,),
            )
            if not isinstance(commits, list):
                raise RuntimeError(f"Commits API returned invalid data for {full_name}")
            for commit in commits:
                committed_at = commit.get("commit", {}).get("author", {}).get("date")
                if isinstance(committed_at, str):
                    dates.append(committed_at)
            if len(commits) < 100:
                break
        else:
            raise RuntimeError(
                f"Commit pagination exceeded 2,000 items for {full_name}"
            )
    return dates


def fetch_profile(username: str, token: str | None) -> dict[str, Any]:
    """GitHub APIからプロフィール描画に必要な公開データを取得する。"""
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError(f"Invalid GitHub username: {username!r}")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required for live profile generation")

    encoded_username = urllib.parse.quote(username, safe="")
    user = _request_json(f"{API_ROOT}/users/{encoded_username}", token)
    repos: list[dict[str, Any]] = []

    for page in range(1, 101):
        page_repos = _request_json(
            f"{API_ROOT}/users/{encoded_username}/repos"
            f"?type=owner&sort=full_name&direction=asc&per_page=100&page={page}",
            token,
        )
        if not isinstance(page_repos, list):
            raise RuntimeError("GitHub API returned an unexpected repositories payload")
        repos.extend(page_repos)
        if len(page_repos) < 100:
            break
    else:
        raise RuntimeError("Repository pagination exceeded the safety limit")

    source_repos = [repo for repo in repos if not repo.get("fork", False)]
    today = datetime.now(timezone.utc).date()
    created_at = datetime.fromisoformat(str(user["created_at"]).replace("Z", "+00:00"))
    contributions = _fetch_contributions(username, created_at.year, today, token)

    return {
        "user": user,
        "repos": repos,
        "language_bytes": _fetch_language_bytes(source_repos, token),
        "contributions": contributions,
        "recent_commit_dates": _fetch_recent_commit_dates(
            contributions["recent_repositories"],
            username,
            contributions["recent_from"],
            token,
        ),
    }


def load_fixture(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fixture_file:
        data = json.load(fixture_file)
    if not isinstance(data, dict) or "user" not in data or "repos" not in data:
        raise ValueError("Fixture must contain 'user' and 'repos'")
    return data


def _recent_month_keys(today: date, count: int = 12) -> list[str]:
    keys: list[str] = []
    year = today.year
    month = today.month
    for _ in range(count):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    return list(reversed(keys))


def calculate_stats(data: dict[str, Any], today: date | None = None) -> ProfileStats:
    user = data["user"]
    repos = data["repos"]
    if not isinstance(user, dict) or not isinstance(repos, list):
        raise ValueError("Profile data has an invalid shape")

    source_repos = [repo for repo in repos if not repo.get("fork", False)]
    language_counts: dict[str, int] = {}
    for repo in source_repos:
        language = repo.get("language")
        if isinstance(language, str) and language:
            language_counts[language] = language_counts.get(language, 0) + 1

    repository_languages = tuple(
        sorted(language_counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    )

    language_bytes = data.get("language_bytes", {})
    if not isinstance(language_bytes, dict):
        raise ValueError("Profile language data has an invalid shape")
    code_languages = tuple(
        sorted(
            ((str(language), int(size)) for language, size in language_bytes.items()),
            key=lambda item: (-item[1], item[0].casefold()),
        )
    )

    created_at = datetime.fromisoformat(str(user["created_at"]).replace("Z", "+00:00"))
    comparison_date = today or datetime.now(timezone.utc).date()
    anniversary_passed = (comparison_date.month, comparison_date.day) >= (
        created_at.month,
        created_at.day,
    )
    years_on_github = max(
        0, comparison_date.year - created_at.year - (not anniversary_passed)
    )

    contributions = data.get("contributions", {})
    if not isinstance(contributions, dict):
        raise ValueError("Profile contribution data has an invalid shape")
    month_totals = {key: 0 for key in _recent_month_keys(comparison_date)}
    for day in contributions.get("days", []):
        month_key = str(day.get("date", ""))[:7]
        if month_key in month_totals:
            month_totals[month_key] += int(day.get("contributionCount", 0))

    jst = timezone(timedelta(hours=9))
    commit_hours = [0] * 24
    for committed_at in data.get("recent_commit_dates", []):
        parsed = datetime.fromisoformat(str(committed_at).replace("Z", "+00:00"))
        commit_hours[parsed.astimezone(jst).hour] += 1

    return ProfileStats(
        public_repos=int(user.get("public_repos", len(repos))),
        original_projects=len(source_repos),
        stars_earned=sum(int(repo.get("stargazers_count", 0)) for repo in source_repos),
        forks_earned=sum(int(repo.get("forks_count", 0)) for repo in source_repos),
        followers=int(user.get("followers", 0)),
        years_on_github=years_on_github,
        repository_languages=repository_languages,
        code_languages=code_languages,
        contribution_months=tuple(month_totals.items()),
        contributions_last_year=int(contributions.get("last_year_total", 0)),
        lifetime_contributions=int(contributions.get("lifetime_total", 0)),
        total_commits=int(contributions.get("total_commits", 0)),
        total_pull_requests=int(contributions.get("total_pull_requests", 0)),
        total_issues=int(contributions.get("total_issues", 0)),
        contributed_repositories=int(
            contributions.get("contributed_repositories", 0)
        ),
        recent_commit_hours=tuple(commit_hours),
        recent_commit_count=sum(commit_hours),
    )


def _compact_number(value: int) -> str:
    if value < 1_000:
        return str(value)
    if value < 1_000_000:
        return f"{value / 1_000:.1f}K".replace(".0K", "K")
    return f"{value / 1_000_000:.1f}M".replace(".0M", "M")


def _shorten(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _trophies(stats: ProfileStats) -> tuple[tuple[str, str, bool], ...]:
    return (
        ("ORBIT MAKER", "25 original projects", stats.original_projects >= 25),
        ("STAR FORGE", "50 stars earned", stats.stars_earned >= 50),
        ("DEEP SPACE", "5 years on GitHub", stats.years_on_github >= 5),
        (
            "POLYGLOT",
            "5 repository languages",
            len(stats.repository_languages) >= 5,
        ),
    )


def _format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB")
    amount = float(value)
    for unit in units:
        if amount < 1_000 or unit == units[-1]:
            return f"{amount:.1f}{unit}".replace(".0B", "B")
        amount /= 1_000
    return str(value)


def _top_with_other(
    items: tuple[tuple[str, int], ...], visible_items: int = 4
) -> tuple[tuple[str, int], ...]:
    if len(items) <= visible_items:
        return items
    shown = list(items[:visible_items])
    shown.append(("Other", sum(value for _, value in items[visible_items:])))
    return tuple(shown)


def _nice_axis_max(value: int) -> int:
    if value <= 0:
        return 4
    rough_step = value / 4
    magnitude = 10 ** math.floor(math.log10(rough_step))
    normalized = rough_step / magnitude
    factor = (
        1
        if normalized <= 1
        else 2
        if normalized <= 2
        else 2.5
        if normalized <= 2.5
        else 5
        if normalized <= 5
        else 10
    )
    return max(4, int(factor * magnitude * 4))


def _smooth_path(points: list[tuple[float, float]]) -> str:
    if not points:
        return ""
    if len(points) == 1:
        return f"M {points[0][0]:.1f} {points[0][1]:.1f}"
    commands = [f"M {points[0][0]:.1f} {points[0][1]:.1f}"]
    for index in range(1, len(points) - 1):
        current = points[index]
        following = points[index + 1]
        midpoint = ((current[0] + following[0]) / 2, (current[1] + following[1]) / 2)
        commands.append(
            f"Q {current[0]:.1f} {current[1]:.1f} {midpoint[0]:.1f} {midpoint[1]:.1f}"
        )
    commands.append(f"T {points[-1][0]:.1f} {points[-1][1]:.1f}")
    return " ".join(commands)


def _donut_markup(
    items: tuple[tuple[str, int], ...],
    *,
    center_x: int,
    center_y: int,
    legend_x: int,
    legend_y: int,
    total_text: str,
    total_caption: str,
    value_suffix: str,
) -> str:
    displayed = _top_with_other(items)
    total = sum(value for _, value in displayed) or 1
    circumference = 2 * math.pi * 78
    rings: list[str] = []
    legends: list[str] = []
    offset = 0.0
    for index, (label, value) in enumerate(displayed):
        percentage = value / total * 100
        segment_length = circumference * percentage / 100
        color = LANGUAGE_COLORS.get(label, "#5d6f91")
        rings.append(
            f'<circle cx="{center_x}" cy="{center_y}" r="78" fill="none" '
            f'stroke="{color}" stroke-width="34" '
            f'stroke-dasharray="{segment_length:.3f} {circumference - segment_length:.3f}" '
            f'stroke-dashoffset="{-circumference * offset / 100:.3f}" '
            f'transform="rotate(-90 {center_x} {center_y})"/>'
        )
        row_y = legend_y + index * 38
        legends.append(
            f'<rect x="{legend_x}" y="{row_y - 12}" width="12" height="12" rx="3" fill="{color}"/>'
            f'<text x="{legend_x + 22}" y="{row_y}" fill="#d5e4f7" font-size="14">{escape(_shorten(label, 15))}</text>'
            f'<text x="{legend_x + 168}" y="{row_y}" text-anchor="end" fill="#7890b0" font-size="12">'
            f'{_compact_number(value)}{value_suffix}</text>'
        )
        offset += percentage

    return f"""
    <circle cx="{center_x}" cy="{center_y}" r="78" fill="none" stroke="#182845" stroke-width="34"/>
    {''.join(rings)}
    <circle cx="{center_x}" cy="{center_y}" r="55" fill="#081226"/>
    <text x="{center_x}" y="{center_y - 3}" text-anchor="middle" fill="#f3f8ff" font-size="24" font-weight="700">{escape(total_text)}</text>
    <text x="{center_x}" y="{center_y + 21}" text-anchor="middle" fill="#7890b0" font-size="11" letter-spacing="1">{escape(total_caption)}</text>
    {''.join(legends)}"""


def render_dashboard(data: dict[str, Any], stats: ProfileStats) -> str:
    user = data["user"]
    login = escape(str(user.get("login", "BlueEventHorizon")))
    display_name = escape(str(user.get("name") or login))
    bio = escape(PROFILE_TAGLINE)

    contribution_values = [value for _, value in stats.contribution_months]
    chart_max = _nice_axis_max(max(contribution_values, default=0))
    chart_left, chart_top, chart_width, chart_height = 500, 124, 620, 148
    chart_bottom = chart_top + chart_height
    point_count = max(1, len(contribution_values) - 1)
    contribution_points = [
        (
            chart_left + index * chart_width / point_count,
            chart_bottom - value / chart_max * chart_height,
        )
        for index, value in enumerate(contribution_values)
    ]
    contribution_line = _smooth_path(contribution_points)
    contribution_area = (
        f"{contribution_line} L {contribution_points[-1][0]:.1f} {chart_bottom} "
        f"L {contribution_points[0][0]:.1f} {chart_bottom} Z"
        if contribution_points
        else ""
    )

    chart_grid: list[str] = []
    for tick in range(5):
        value = chart_max * tick // 4
        y = chart_bottom - chart_height * tick / 4
        chart_grid.append(
            f'<path d="M {chart_left} {y:.1f} H {chart_left + chart_width}" stroke="#1d3153"/>'
            f'<text x="{chart_left + chart_width + 10}" y="{y + 5:.1f}" fill="#6f86a7" font-size="11">{value}</text>'
        )

    month_labels: list[str] = []
    for index, (month, _) in enumerate(stats.contribution_months):
        if index % 2 == 0 or index == len(stats.contribution_months) - 1:
            x = chart_left + index * chart_width / point_count
            month_labels.append(
                f'<text x="{x:.1f}" y="{chart_bottom + 24}" text-anchor="middle" fill="#6f86a7" font-size="11">{month[2:].replace("-", "/")}</text>'
            )

    repo_donut = _donut_markup(
        stats.repository_languages,
        center_x=422,
        center_y=532,
        legend_x=70,
        legend_y=450,
        total_text=str(sum(value for _, value in stats.repository_languages)),
        total_caption="PROJECTS INDEXED",
        value_suffix=" repos",
    )
    code_donut = _donut_markup(
        stats.code_languages,
        center_x=1005,
        center_y=532,
        legend_x=654,
        legend_y=450,
        total_text=_format_bytes(sum(value for _, value in stats.code_languages)),
        total_caption="CODE INDEXED",
        value_suffix="B",
    )

    stats_rows = (
        ("TOTAL CONTRIBUTIONS", stats.lifetime_contributions),
        ("PUBLIC COMMITS", stats.total_commits),
        ("PULL REQUESTS", stats.total_pull_requests),
        ("ISSUES OPENED", stats.total_issues),
        ("REPOS WITH COMMITS", stats.contributed_repositories),
        ("STARS EARNED", stats.stars_earned),
    )
    stats_markup: list[str] = []
    for index, (label, value) in enumerate(stats_rows):
        column = index // 3
        row = index % 3
        x = 70 + column * 260
        y = 812 + row * 66
        stats_markup.append(
            f'<circle cx="{x}" cy="{y - 5}" r="5" fill="#48d7ff"/>'
            f'<text x="{x + 18}" y="{y}" fill="#7890b0" font-size="12" letter-spacing="1">{label}</text>'
            f'<text x="{x + 18}" y="{y + 31}" fill="#f3f8ff" font-size="24" font-weight="700">{_compact_number(value)}</text>'
        )

    hour_max = _nice_axis_max(max(stats.recent_commit_hours, default=0))
    histogram_left, histogram_top = 654, 810
    histogram_width, histogram_height = 474, 176
    slot_width = histogram_width / 24
    histogram_bars: list[str] = []
    for hour, value in enumerate(stats.recent_commit_hours):
        bar_height = value / hour_max * histogram_height
        histogram_bars.append(
            f'<rect x="{histogram_left + hour * slot_width + 2:.1f}" '
            f'y="{histogram_top + histogram_height - bar_height:.1f}" '
            f'width="{max(3, slot_width - 4):.1f}" height="{bar_height:.1f}" '
            f'rx="2" fill="url(#barGradient)"/>'
        )
    histogram_grid: list[str] = []
    for tick in range(5):
        value = hour_max * tick // 4
        y = histogram_top + histogram_height - histogram_height * tick / 4
        histogram_grid.append(
            f'<path d="M {histogram_left} {y:.1f} H {histogram_left + histogram_width}" stroke="#1d3153"/>'
            f'<text x="{histogram_left - 12}" y="{y + 5:.1f}" text-anchor="end" fill="#6f86a7" font-size="11">{value}</text>'
        )

    trophy_cards: list[str] = []
    for index, (name, requirement, unlocked) in enumerate(_trophies(stats)):
        x = 50 + index * 284
        status_color = "#48d7ff" if unlocked else "#445570"
        opacity = "1" if unlocked else ".5"
        trophy_cards.append(
            f"""
    <g transform="translate({x} 1094)" opacity="{opacity}">
      <rect width="252" height="92" rx="16" fill="#0d1730" stroke="{status_color}" stroke-opacity=".75"/>
      <circle cx="30" cy="31" r="13" fill="none" stroke="{status_color}" stroke-width="2"/>
      <path d="M24 31 H36 M30 25 V37" stroke="{status_color}" stroke-width="2"/>
      <text x="54" y="31" fill="#f3f8ff" font-size="14" font-weight="700" letter-spacing="1">{name}</text>
      <text x="54" y="55" fill="#7890b0" font-size="11">{escape(requirement)}</text>
      <text x="54" y="75" fill="{status_color}" font-size="9" letter-spacing="2">{'UNLOCKED' if unlocked else 'LOCKED'}</text>
    </g>"""
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1240" viewBox="0 0 1200 1240" role="img" aria-labelledby="title description" font-family="Arial, Helvetica, sans-serif">
  <title id="title">{login} GitHub profile dashboard</title>
  <desc id="description">GitHub contributions, languages, statistics, commit hours, and achievements for {login}.</desc>
  <defs>
    <linearGradient id="background" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#030611"/><stop offset=".55" stop-color="#07132b"/><stop offset="1" stop-color="#10143b"/>
    </linearGradient>
    <linearGradient id="horizon" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#48d7ff" stop-opacity="0"/><stop offset=".45" stop-color="#48d7ff"/><stop offset=".72" stop-color="#a45cff"/><stop offset="1" stop-color="#ff4fd8" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#a45cff" stop-opacity=".82"/><stop offset="1" stop-color="#48d7ff" stop-opacity=".06"/>
    </linearGradient>
    <linearGradient id="barGradient" x1="0" y1="1" x2="0" y2="0">
      <stop offset="0" stop-color="#48d7ff"/><stop offset="1" stop-color="#a45cff"/>
    </linearGradient>
    <radialGradient id="orb">
      <stop offset="0" stop-color="#010207"/><stop offset=".58" stop-color="#020308"/><stop offset=".75" stop-color="#8c5cff" stop-opacity=".35"/><stop offset="1" stop-color="#48d7ff" stop-opacity="0"/>
    </radialGradient>
    <filter id="softGlow" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="7"/></filter>
  </defs>

  <rect width="1200" height="1240" rx="28" fill="url(#background)"/>
  <rect x="1" y="1" width="1198" height="1238" rx="27" fill="none" stroke="#263d67"/>
  <g fill="#bcefff" opacity=".72">
    <circle cx="78" cy="74" r="1.2"/><circle cx="286" cy="47" r="1"/><circle cx="462" cy="100" r="1.4"/>
    <circle cx="748" cy="58" r="1"/><circle cx="1107" cy="47" r="1.3"/><circle cx="1140" cy="338" r="1"/>
    <circle cx="82" cy="687" r="1.3"/><circle cx="592" cy="690" r="1"/><circle cx="1118" cy="1037" r="1.2"/>
  </g>

  <rect x="30" y="30" width="1140" height="300" rx="24" fill="#091328" stroke="#223a63"/>
  <text x="64" y="69" fill="#48d7ff" font-family="ui-monospace, SFMono-Regular, Menlo, monospace" font-size="11" letter-spacing="3">PROFILE SYSTEM // ONLINE</text>
  <text x="64" y="114" fill="#f3f8ff" font-size="36" font-weight="800" letter-spacing="2">BLUE EVENT HORIZON</text>
  <path d="M64 130 H438" stroke="url(#horizon)" stroke-width="2"/>
  <text x="64" y="159" fill="#a8bdd8" font-size="15">{display_name} · @{login}</text>
  <text x="64" y="184" fill="#7890b0" font-size="12">{bio}</text>
  <text x="64" y="224" fill="#f3f8ff" font-size="24" font-weight="700">{_compact_number(stats.contributions_last_year)}</text>
  <text x="165" y="224" fill="#7890b0" font-size="12" letter-spacing="1">CONTRIBUTIONS // LAST YEAR</text>
  <text x="64" y="255" fill="#a8bdd8" font-size="13">{stats.original_projects} original projects · {stats.public_repos} public repos</text>
  <text x="64" y="280" fill="#a8bdd8" font-size="13">{stats.followers} followers · joined {stats.years_on_github} years ago</text>
  <g opacity=".95">
    <ellipse cx="430" cy="52" rx="48" ry="6" fill="none" stroke="url(#horizon)" stroke-width="5" transform="rotate(-10 430 52)" filter="url(#softGlow)"/>
    <ellipse cx="430" cy="52" rx="48" ry="6" fill="none" stroke="url(#horizon)" transform="rotate(-10 430 52)"/>
    <circle cx="430" cy="52" r="25" fill="url(#orb)"/><circle cx="430" cy="52" r="11" fill="#010207"/>
  </g>

  <text x="500" y="73" fill="#a45cff" font-family="ui-monospace, SFMono-Regular, Menlo, monospace" font-size="11" letter-spacing="3">CONTRIBUTION ORBIT // 12 MONTHS</text>
  {''.join(chart_grid)}
  <path d="{contribution_area}" fill="url(#areaGradient)"/>
  <path d="{contribution_line}" fill="none" stroke="#a45cff" stroke-width="3"/>
  {''.join(month_labels)}

  <rect x="30" y="354" width="554" height="326" rx="22" fill="#091328" stroke="#223a63"/>
  <text x="64" y="401" fill="#48d7ff" font-size="20" font-weight="700">Languages by Repository</text>
  <text x="64" y="424" fill="#7890b0" font-size="11" letter-spacing="1">PRIMARY LANGUAGE · FORKS EXCLUDED</text>
  {repo_donut}

  <rect x="616" y="354" width="554" height="326" rx="22" fill="#091328" stroke="#223a63"/>
  <text x="650" y="401" fill="#a45cff" font-size="20" font-weight="700">Languages by Code Size</text>
  <text x="650" y="424" fill="#7890b0" font-size="11" letter-spacing="1">GITHUB LINGUIST BYTES · FORKS EXCLUDED</text>
  {code_donut}

  <rect x="30" y="704" width="554" height="330" rx="22" fill="#091328" stroke="#223a63"/>
  <text x="64" y="751" fill="#48d7ff" font-size="20" font-weight="700">Lifetime Signals</text>
  <text x="64" y="774" fill="#7890b0" font-size="11" letter-spacing="1">GITHUB CONTRIBUTION HISTORY</text>
  {''.join(stats_markup)}

  <rect x="616" y="704" width="554" height="330" rx="22" fill="#091328" stroke="#223a63"/>
  <text x="650" y="751" fill="#a45cff" font-size="20" font-weight="700">Recent Public Commits</text>
  <text x="650" y="774" fill="#7890b0" font-size="11" letter-spacing="1">LAST 365 DAYS · JST (UTC+9) · {stats.recent_commit_count} COMMITS</text>
  {''.join(histogram_grid)}
  {''.join(histogram_bars)}
  <path d="M {histogram_left} {histogram_top} V {histogram_top + histogram_height} H {histogram_left + histogram_width}" fill="none" stroke="#607a9e"/>
  <text x="{histogram_left}" y="1010" fill="#6f86a7" font-size="11">00</text>
  <text x="{histogram_left + histogram_width * .25}" y="1010" text-anchor="middle" fill="#6f86a7" font-size="11">06</text>
  <text x="{histogram_left + histogram_width * .5}" y="1010" text-anchor="middle" fill="#6f86a7" font-size="11">12</text>
  <text x="{histogram_left + histogram_width * .75}" y="1010" text-anchor="middle" fill="#6f86a7" font-size="11">18</text>
  <text x="{histogram_left + histogram_width}" y="1010" text-anchor="end" fill="#6f86a7" font-size="11">23</text>

  <text x="34" y="1074" fill="#a45cff" font-family="ui-monospace, SFMono-Regular, Menlo, monospace" font-size="11" letter-spacing="3">ACHIEVEMENTS // EVENT HORIZON MILESTONES</text>
  {''.join(trophy_cards)}

  <path d="M42 1210 H1158" stroke="#1d3154"/>
  <text x="42" y="1228" fill="#4e6688" font-family="ui-monospace, SFMono-Regular, Menlo, monospace" font-size="9" letter-spacing="2">GITHUB-NATIVE TELEMETRY · WEEKLY SNAPSHOT</text>
  <text x="1158" y="1228" text-anchor="end" fill="#4e6688" font-family="ui-monospace, SFMono-Regular, Menlo, monospace" font-size="9">NO THIRD-PARTY STATS SERVICE</text>
</svg>
"""


def write_if_changed(path: Path, content: str) -> bool:
    """内容が変化した場合だけ安全にファイルを書き換える。"""
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(path)
    return True


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="BlueEventHorizon")
    parser.add_argument(
        "--output", type=Path, default=Path("assets/github-dashboard.svg")
    )
    parser.add_argument("--fixture", type=Path, help="GitHub APIの代わりにJSONを使う")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    data = (
        load_fixture(args.fixture)
        if args.fixture
        else fetch_profile(args.username, os.environ.get("GITHUB_TOKEN"))
    )
    stats = calculate_stats(data)
    changed = write_if_changed(args.output, render_dashboard(data, stats))
    print(f"{'updated' if changed else 'unchanged'}: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
