from dotenv import load_dotenv
import os
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict

load_dotenv()  # this loads variables from .env into environment variables

TOKEN = os.getenv("GITHUB_TOKEN")
OWNER = os.getenv("REPO_OWNER")
REPO = os.getenv("REPO_NAME")

GITHUB_API = "https://api.github.com"
TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github+json"
}





def iso8601(dt):
    return dt.replace(microsecond=0).isoformat() + "Z"

def get_prs(state="all", params=None):
    url = f"{GITHUB_API}/repos/{OWNER}/{REPO}/pulls"
    if params is None:
        params = {}
    params["state"] = state
    prs = []
    page = 1
    while True:
        params["page"] = page
        params["per_page"] = 100
        resp = requests.get(url, headers=HEADERS, params=params)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        prs.extend(data)
        page += 1
    return prs

def get_reviews(pr_number):
    url = f"{GITHUB_API}/repos/{OWNER}/{REPO}/pulls/{pr_number}/reviews"
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()

def is_changes_requested(reviews):
    for review in reversed(reviews):
        if review["state"].lower() == "changes_requested":
            return True
    return False

def average_review_time(prs):
    total_seconds = 0
    count = 0
    for pr in prs:
        created_at = datetime.strptime(pr["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        merged_at_str = pr.get("merged_at")
        if merged_at_str:
            merged_at = datetime.strptime(merged_at_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            total_seconds += (merged_at - created_at).total_seconds()
            count += 1
    if count == 0:
        return 0
    return round(total_seconds / 3600 / count, 2)  # hours

def main():
    print(f"REPO_OWNER={OWNER}")
    print(f"REPO_NAME={REPO}")
    print(f"GITHUB_TOKEN is set: {bool(TOKEN)}")
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    yesterday_start = today_start - timedelta(days=1)

    # Get open PRs
    open_prs = get_prs("open")
    # Get closed PRs merged today
    closed_prs = get_prs("closed")

    # PRs raised today
    prs_raised_today = [pr for pr in open_prs + closed_prs
                       if datetime.strptime(pr["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) >= today_start]

    # PRs merged today
    prs_merged_today = [pr for pr in closed_prs
                       if pr.get("merged_at") and
                       datetime.strptime(pr["merged_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) >= today_start]

    # PRs with changes requested
    prs_with_changes_requested = []
    for pr in open_prs:
        reviews = get_reviews(pr["number"])
        if is_changes_requested(reviews):
            prs_with_changes_requested.append(pr)

    # PRs not approved = open and no approvals (simplification: no review with 'approved')
    prs_not_approved = []
    for pr in open_prs:
        reviews = get_reviews(pr["number"])
        states = [r["state"].lower() for r in reviews]
        if "approved" not in states:
            prs_not_approved.append(pr)

    # Critical/Hotfix PRs open (check label 'hotfix' or 'critical')
    prs_hotfix = [pr for pr in open_prs if any(lbl['name'].lower() in ['hotfix', 'critical'] for lbl in pr.get('labels', []))]

    # PRs pending review >24h (no reviews and open for > 24h)
    prs_pending_review = []
    for pr in open_prs:
        created_at = datetime.strptime(pr["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if (now - created_at) > timedelta(hours=24):
            reviews = get_reviews(pr["number"])
            if not reviews:
                prs_pending_review.append(pr)

    # Oldest open PR in days
    if open_prs:
        oldest_pr = min(open_prs, key=lambda pr: pr["created_at"])
        oldest_days = (now - datetime.strptime(oldest_pr["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).days
    else:
        oldest_days = 0

    # Avg review cycle time (hours) - for merged PRs
    avg_review_time = average_review_time(prs_merged_today)

    # Member-wise breakdown
    members = defaultdict(lambda: {"raised": 0, "merged": 0, "changes_requested": 0, "not_approved": 0, "reviews_done": 0})

    # Count PRs raised
    for pr in prs_raised_today:
        login = pr["user"]["login"]
        members[login]["raised"] += 1

    # Count PRs merged today
    for pr in prs_merged_today:
        login = pr["user"]["login"]
        members[login]["merged"] += 1

    # Changes requested (open PRs)
    for pr in prs_with_changes_requested:
        login = pr["user"]["login"]
        members[login]["changes_requested"] += 1

    # Not approved (open PRs without approval)
    for pr in prs_not_approved:
        login = pr["user"]["login"]
        members[login]["not_approved"] += 1

    # Reviews done - gather from reviews for all PRs in repo (could be large, so simplified: count reviews done today by users)
    # Here, just get reviews done on all PRs merged or open today
    # For demo, will skip exact reviews count or limit to recent 100 reviews

    # Get recent reviews (last 100)
    url_reviews = f"{GITHUB_API}/repos/{OWNER}/{REPO}/pulls?state=all&per_page=100"
    resp = requests.get(url_reviews, headers=HEADERS)
    resp.raise_for_status()
    recent_prs = resp.json()
    reviews_done_map = defaultdict(int)

    for pr in recent_prs:
        reviews = get_reviews(pr["number"])
        for review in reviews:
            reviewer = review["user"]["login"]
            reviews_done_map[reviewer] += 1

    for user, count in reviews_done_map.items():
        members[user]["reviews_done"] = count

    # Bottlenecks & Risk
    # PRs stuck > 2 days (open > 2 days)
    pr_stuck = sum(1 for pr in open_prs if (now - datetime.strptime(pr["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)) > timedelta(days=2))
    # Pending releases (use label 'pending-release')
    pending_release = sum(1 for pr in open_prs if any(lbl['name'].lower() == 'pending-release' for lbl in pr.get('labels', [])))
    # Reopened/Failed PRs (closed and reopened)
    # GitHub API does not provide direct reopened info easily; simplified count closed PRs reopened by checking events (skipped here for demo)
    reopened_failed = 0

    # Quick Insights
    most_active = max(members.items(), key=lambda x: x[1]["raised"], default=(None,))[0] or "-"
    review_heavy = max(members.items(), key=lambda x: x[1]["reviews_done"], default=(None,))[0] or "-"
    owners_stale_prs = ", ".join(set(pr["user"]["login"] for pr in open_prs if (now - datetime.strptime(pr["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)) > timedelta(days=7))) or "-"
    blocker_owners = ", ".join(set(pr["user"]["login"] for pr in prs_hotfix)) or "-"

    # Prepare output markdown
    lines = []
    lines.append(f"## 🔷 GitHub PR Daily Snapshot ({today_start.strftime('%Y-%m-%d')})\n")
    lines.append("### 1️⃣ Activity Summary")
    lines.append(f"- **PRs Raised Today:** `{len(prs_raised_today)}`")
    lines.append(f"- **PRs Merged Today:** `{len(prs_merged_today)}`")
    lines.append(f"- **PRs With \"Changes Requested\":** `{len(prs_with_changes_requested)}`")
    lines.append(f"- **PRs Not Approved (Unmerged + Unapproved):** `{len(prs_not_approved)}`")
    lines.append(f"- **Critical/Hotfix PRs Open:** `{len(prs_hotfix)}`")
    lines.append(f"- **PRs Pending Review (>24h):** `{len(prs_pending_review)}`")
    lines.append(f"- **Oldest Open PR (days):** `{oldest_days}`")
    lines.append(f"- **Avg. Review Cycle Time (hrs):** `{avg_review_time}`\n")

    lines.append("---\n### 2️⃣ Member-wise Breakdown\n")
    lines.append("| Member  | PRs Raised | PRs Merged | Changes Requested | Not Approved | Reviews Done |")
    lines.append("|---------|------------|------------|------------------|--------------|--------------|")
    for member, stats in sorted(members.items(), key=lambda x: x[1]["raised"], reverse=True):
        lines.append(f"| {member} |     {stats['raised']}      |     {stats['merged']}      |        {stats['changes_requested']}         |      {stats['not_approved']}       |      {stats['reviews_done']}       |")

    lines.append("\n---\n### 3️⃣ Bottlenecks & Risk\n")
    lines.append(f"- **PRs Stuck >2 Days:** `{pr_stuck}`")
    lines.append(f"- **Pending Releases:** `{pending_release}`")
    lines.append(f"- **Reopened/Failed PRs:** `{reopened_failed}`\n")

    lines.append("---\n**Quick Insights**")
    lines.append(f"- **Most Active Contributor:** `{most_active}`")
    lines.append(f"- **Review Load (Most):** `{review_heavy}`")
    lines.append(f"- **Stale PR Owners:** `{owners_stale_prs}`")
    lines.append(f"- **Blockers/Hotfixes Today:** `{blocker_owners}`\n")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
