import os
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("GITHUB_TOKEN")
if not TOKEN:
    raise Exception("GITHUB_TOKEN not set in environment or .env file")

HEADERS = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github+json"
}

def iso_to_dt(iso_str):
    return datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

def get_all_repos():
    repos = []
    page = 1
    while True:
        url = f"https://api.github.com/user/repos?per_page=100&page={page}"
        resp = requests.get(url, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        repos.extend(data)
        page += 1
    return repos

def get_prs(owner, repo, state="open"):
    prs = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
        params = {"state": state, "per_page": 100, "page": page}
        resp = requests.get(url, headers=HEADERS, params=params)
        if resp.status_code == 404:
            # Possibly repo archived or no access
            return []
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        prs.extend(data)
        page += 1
    return prs

def get_reviews(owner, repo, pr_number):
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return resp.json()

def main():
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    repos = get_all_repos()
    if not repos:
        print("No repos found for user.")
        return

    # Aggregate totals across all repos
    total_raised = 0
    total_merged = 0
    total_changes_requested = 0
    total_not_approved = 0
    total_hotfix = 0
    total_pending_24h = 0
    pr_stuck = 0
    pending_release = 0
    reopened_failed = 0  # skipping for complexity

    oldest_open_days = 0
    oldest_open_date = None
    review_cycle_times = []
    
    # Member-wise list of dicts with RepoName
    member_rows = []

    for repo in repos:
        owner = repo["owner"]["login"]
        repo_name = repo["name"]
        try:
            open_prs = get_prs(owner, repo_name, "open")
            closed_prs = get_prs(owner, repo_name, "closed")
        except Exception as e:
            # Skip repo on error
            continue

        prs_raised_today = [pr for pr in open_prs + closed_prs if iso_to_dt(pr["created_at"]) >= today_start]
        prs_merged_today = [pr for pr in closed_prs if pr.get("merged_at") and iso_to_dt(pr["merged_at"]) >= today_start]

        total_raised += len(prs_raised_today)
        total_merged += len(prs_merged_today)

        prs_with_changes_requested = []
        prs_not_approved = []
        prs_hotfix = []
        prs_pending_review = []

        for pr in open_prs:
            reviews = get_reviews(owner, repo_name, pr["number"])
            states = [r["state"].lower() for r in reviews]

            if "changes_requested" in states:
                prs_with_changes_requested.append(pr)
            if "approved" not in states:
                prs_not_approved.append(pr)
            if any(lbl['name'].lower() in ['hotfix', 'critical'] for lbl in pr.get('labels', [])):
                prs_hotfix.append(pr)

            created_at = iso_to_dt(pr["created_at"])
            if (now - created_at) > timedelta(hours=24) and len(reviews) == 0:
                prs_pending_review.append(pr)
            if (now - created_at) > timedelta(days=2):
                pr_stuck += 1
            if (now - created_at) > timedelta(days=7):
                # Track stale PR owners later
                pass
            if any(lbl['name'].lower() == 'pending-release' for lbl in pr.get('labels', [])):
                pending_release += 1

        total_changes_requested += len(prs_with_changes_requested)
        total_not_approved += len(prs_not_approved)
        total_hotfix += len(prs_hotfix)
        total_pending_24h += len(prs_pending_review)

        # Track oldest open PR date
        if open_prs:
            repo_oldest = min(open_prs, key=lambda pr: pr["created_at"])
            repo_oldest_date = iso_to_dt(repo_oldest["created_at"])
            if oldest_open_date is None or repo_oldest_date < oldest_open_date:
                oldest_open_date = repo_oldest_date

        # Avg review cycle time from merged PRs
        for pr in prs_merged_today:
            created = iso_to_dt(pr["created_at"])
            merged = iso_to_dt(pr["merged_at"])
            review_cycle_times.append((merged - created).total_seconds() / 3600)

        # Member-wise aggregation per repo
        members = defaultdict(lambda: {"raised": 0, "merged": 0, "changes_requested": 0,
                                       "not_approved": 0, "reviews_done": 0})

        for pr in prs_raised_today:
            members[pr["user"]["login"]]["raised"] += 1
        for pr in prs_merged_today:
            members[pr["user"]["login"]]["merged"] += 1
        for pr in prs_with_changes_requested:
            members[pr["user"]["login"]]["changes_requested"] += 1
        for pr in prs_not_approved:
            members[pr["user"]["login"]]["not_approved"] += 1

        # Reviews done (count reviews on today's PRs)
        pr_ids_to_check = [pr["number"] for pr in prs_raised_today + prs_merged_today]
        reviews_done_map = defaultdict(int)
        for pr_id in pr_ids_to_check:
            reviews = get_reviews(owner, repo_name, pr_id)
            for r in reviews:
                reviewer = r["user"]["login"]
                reviews_done_map[reviewer] += 1
        for user, count_r in reviews_done_map.items():
            members[user]["reviews_done"] = count_r

        # Append member rows with repo name
        for member, stats in members.items():
            member_rows.append({
                "Member": member,
                "PRs Raised": stats["raised"],
                "PRs Merged": stats["merged"],
                "Changes Requested": stats["changes_requested"],
                "Not Approved": stats["not_approved"],
                "Reviews Done": stats["reviews_done"],
                "RepoName": repo_name
            })

    # Calculate oldest open PR days
    if oldest_open_date:
        oldest_open_days = (now - oldest_open_date).days
    else:
        oldest_open_days = 0

    avg_review_time = round(sum(review_cycle_times) / len(review_cycle_times), 2) if review_cycle_times else 0

    # Collect stale PR owners (open > 7 days)
    stale_pr_owners = set()
    for repo in repos:
        owner = repo["owner"]["login"]
        repo_name = repo["name"]
        try:
            open_prs = get_prs(owner, repo_name, "open")
        except Exception:
            continue
        for pr in open_prs:
            created_at = iso_to_dt(pr["created_at"])
            if (now - created_at) > timedelta(days=7):
                stale_pr_owners.add(pr["user"]["login"])
    owners_stale_prs = ", ".join(sorted(stale_pr_owners)) if stale_pr_owners else "-"

    # Quick insights
    if member_rows:
        most_active = max(member_rows, key=lambda x: x["PRs Raised"])["Member"]
        review_heavy = max(member_rows, key=lambda x: x["Reviews Done"])["Member"]
    else:
        most_active = "-"
        review_heavy = "-"
    blocker_owners = "-"  # Could be improved by tracking users with hotfix PRs

    # Prepare markdown output
    md = []
    md.append(f"## 🔷 GitHub PR Daily Snapshot ({today_start.strftime('%Y-%m-%d')})\n")
    md.append("### 1️⃣ Activity Summary")
    md.append(f"- **PRs Raised Today:** `{total_raised}`")
    md.append(f"- **PRs Merged Today:** `{total_merged}`")
    md.append(f"- **PRs With \"Changes Requested\":** `{total_changes_requested}`")
    md.append(f"- **PRs Not Approved (Unmerged + Unapproved):** `{total_not_approved}`")
    md.append(f"- **Critical/Hotfix PRs Open:** `{total_hotfix}`")
    md.append(f"- **PRs Pending Review (>24h):** `{total_pending_24h}`")
    md.append(f"- **Oldest Open PR (days):** `{oldest_open_days}`")
    md.append(f"- **Avg. Review Cycle Time (hrs):** `{avg_review_time}`\n---")

    md.append("### 2️⃣ Member-wise Breakdown\n")
    md.append("| Member  | PRs Raised | PRs Merged | Changes Requested | Not Approved | Reviews Done | RepoName |")
    md.append("|---------|------------|------------|------------------|--------------|--------------|----------|")
    for row in member_rows:
        md.append(f"| {row['Member']} |     {row['PRs Raised']}      |     {row['PRs Merged']}      |        {row['Changes Requested']}         |      {row['Not Approved']}       |      {row['Reviews Done']}       | {row['RepoName']} |")

    md.append("\n---")
    md.append("### 3️⃣ Bottlenecks & Risk\n")
    md.append(f"- **PRs Stuck >2 Days:** `{pr_stuck}`")
    md.append(f"- **Pending Releases:** `{pending_release}`")
    md.append(f"- **Reopened/Failed PRs:** `{reopened_failed}`\n---")

    md.append("**Quick Insights**")
    md.append(f"- **Most Active Contributor:** `{most_active}`")
    md.append(f"- **Review Load (Most):** `{review_heavy}`")
    md.append(f"- **Stale PR Owners:** `{owners_stale_prs}`")
    md.append(f"- **Blockers/Hotfixes Today:** `{blocker_owners}`\n---")

    print("\n".join(md))


if __name__ == "__main__":
    main()
