#!/usr/bin/env python3
import datetime
import json
import os
import re
import subprocess

README_PATH = os.environ.get("README_PATH", "README.md")
AUTHOR = os.environ.get("CONTRIB_AUTHOR", "rapsealk")
EXCLUDE_USERS = [u for u in os.environ.get("CONTRIB_EXCLUDE_USERS", "rapsealk,0x0184").split(",") if u]
EXCLUDE_ORGS = [o for o in os.environ.get("CONTRIB_EXCLUDE_ORGS", "lablup,mercy-project,GDG-Seoul").split(",") if o]
ISSUE_TITLE_PREFIX = "Contribution list drift detected"
# The Contributions list is curated (top highlights only), so drift cannot be
# computed as merged-minus-README; the watermark is the latest drift issue's
# date and falls back to the day the list was curated.
INITIAL_WATERMARK = os.environ.get("CONTRIB_INITIAL_WATERMARK", "2026-11-26")
PR_LINK = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")


def gh(*args: str, input: str | None = None) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, input=input).stdout


def gh_json(*args: str, input: str | None = None):
    return json.loads(gh(*args, input=input))


def split_repo(repo_url: str) -> str:
    owner, name = repo_url.rstrip("/").rsplit("/", 2)[-2:]
    return f"{owner}/{name}"


def canonical_repo(owner_repo: str, cache: dict[str, str | None]) -> str:
    # Search results carry current names (meta/astryx) while the README may
    # link renamed ones (facebook/astryx); resolve each through the API so the
    # diff does not flag renames as drift.
    if owner_repo not in cache:
        try:
            cache[owner_repo] = gh_json("api", f"repos/{owner_repo}")["full_name"]
        except subprocess.CalledProcessError:
            cache[owner_repo] = None
    return cache[owner_repo] or owner_repo


def merged_prs(since: str) -> set[tuple[str, int]]:
    q = f"is:pr is:merged author:{AUTHOR} merged:>{since}"
    q += "".join(f" -user:{u}" for u in EXCLUDE_USERS)
    q += "".join(f" -org:{o}" for o in EXCLUDE_ORGS)
    pages = gh_json(
        "api", "-X", "GET", "search/issues", "-f", f"q={q}", "-f", "per_page=100",
        "--paginate", "--slurp",
    )
    return {(split_repo(item["repository_url"]), item["number"]) for page in pages for item in page["items"]}


def readme_prs() -> set[tuple[str, int]]:
    with open(README_PATH, encoding="utf-8") as f:
        body = f.read()
    return {(f"{owner}/{repo}", int(number)) for owner, repo, number in PR_LINK.findall(body)}


def pr_is_merged(owner_repo: str, number: int) -> bool:
    try:
        return bool(gh_json("api", f"repos/{owner_repo}/pulls/{number}")["merged"])
    except subprocess.CalledProcessError:
        return False


def drift(since: str) -> tuple[list[str], list[str]]:
    merged = merged_prs(since)
    cache: dict[str, str | None] = {}
    readme_raw = sorted(readme_prs())
    readme = {(canonical_repo(owner_repo, cache), number) for owner_repo, number in readme_raw}
    new = sorted(f"{owner_repo}#{number}" for owner_repo, number in merged - readme)
    stale = sorted(
        f"{owner_repo}#{number}"
        for owner_repo, number in readme_raw
        if (owner_repo, number) not in merged and not pr_is_merged(owner_repo, number)
    )
    return new, stale


def main() -> None:
    issues = gh_json("issue", "list", "--state", "all", "--limit", "100", "--json", "number,title,state,createdAt")
    drift_dates = [issue["createdAt"][:10] for issue in issues if issue["title"].startswith(ISSUE_TITLE_PREFIX)]
    since = max(drift_dates, key=datetime.date.fromisoformat) if drift_dates else INITIAL_WATERMARK
    new, stale = drift(since)
    if not new and not stale:
        print(f"No drift since {since}")
        return
    sections = []
    if new:
        sections.append(f"## Merged PRs since {since} to consider for the list\n\n" + "\n".join(f"- {item}" for item in new))
    if stale:
        sections.append("## README entries whose PR is not merged\n\n" + "\n".join(f"- {item}" for item in stale))
    print("\n\n".join(sections))
    if any(issue["state"] == "open" and issue["title"].startswith(ISSUE_TITLE_PREFIX) for issue in issues):
        print("An open drift issue already exists; not filing another.")
        return
    title = f"{ISSUE_TITLE_PREFIX} ({datetime.date.today().isoformat()})"
    body = f"The Contributions section in README.md diverged from merged PRs since {since}:\n\n" + "\n\n".join(sections)
    gh("issue", "create", "--title", title, "--body", body)
    print(f"Filed issue: {title}")


if __name__ == "__main__":
    main()
