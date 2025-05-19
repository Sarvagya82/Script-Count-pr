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

def format_report_markdown(
    date_str,
    total_raised,
    total_merged,
    total_changes_requested,
    total_not_approved,
    total_hotfix,
    pending_24h,
    oldest_open,
    avg_review_time,
    member_rows,
    pr_stuck,
    pending_release,
    reopened_failed,
    most_active,
    review_heavy,
    owners_stale_prs,
    blocker_owners
):
    md = []
    md.append(f"📘 **GitHub PR Daily Snapshot ({date_str})**\n")

    md.append("🔹 *Activity Summary*")
    md.append(f"- PRs Raised Today: `{total_raised}`")
    md.append(f"- PRs Merged Today: `{total_merged}`")
    md.append(f"- PRs With Changes Requested: `{total_changes_requested}`")
    md.append(f"- PRs Not Approved: `{total_not_approved}`")
    md.append(f"- Critical/Hotfix PRs Open: `{total_hotfix}`")
    md.append(f"- PRs Pending Review (>24h): `{pending_24h}`")
    md.append(f"- Oldest Open PR (days): `{oldest_open}`")
    md.append(f"- Avg. Review Cycle Time (hrs): `{avg_review_time}`\n")

    md.append("👤 *Member-wise Breakdown*")
    md.append("| Member | Raised | Merged | Changes Requested | Not Approved | Reviews Done | Repo |")
    md.append("|--------|--------|--------|--------------------|--------------|---------------|------|")
    for row in member_rows:
        md.append(
            f"| {row['Member']} | {row['PRs Raised']} | {row['PRs Merged']} | {row['Changes Requested']} | {row['Not Approved']} | {row['Reviews Done']} | {row['RepoName']} |"
        )

    md.append("\n🚨 *Bottlenecks & Risk*")
    md.append(f"- PRs Stuck >2 Days: `{pr_stuck}`")
    md.append(f"- Pending Releases: `{pending_release}`")
    md.append(f"- Reopened/Failed PRs: `{reopened_failed}`\n")

    md.append("✨ *Quick Insights*")
    md.append(f"- Most Active Contributor: `{most_active}`")
    md.append(f"- Review Load (Most): `{review_heavy}`")
    md.append(f"- Stale PR Owners: `{owners_stale_prs}`")
    md.append(f"- Blockers/Hotfixes Today: `{blocker_owners}`")

    return "\n".join(md)

def send_to_google_chat_via_webhook(report_text):
    webhook_url = os.environ.get("GOOGLE_CHAT_WEBHOOK")
    if not webhook_url:
        print("❌ Missing webhook URL.")
        return

    payload = {
        "text": report_text[:4000]  # Google Chat max text limit
    }

    resp = requests.post(webhook_url, json=payload)
    if resp.status_code != 200:
        print("❌ Failed to send message:", resp.text)
    else:
        print("✅ Message sent to Google Chat.")

def main():
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    date_str = today_start.strftime('%Y-%m-%d')

    repos = get_all_repos()
    if not repos:
        print("No repos found.")
        return

    total_raised = total_merged = total_changes_requested = 0
    total_not_approved = total_hotfix = total_pending_24h = 0
    pr_stuck = pending_release = reopened_failed = 0
    oldest_open_date = None
    review_cycle_times = []

    member_rows = []

    for repo in repos:
        owner = repo["owner"]["login"]
        repo_name = repo["name"]
        try:
            open_prs = get_prs(owner, repo_name, "open")
            closed_prs = get_prs(owner, repo_name, "closed")
        except Exception:
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
            if any(lbl['name'].lower() == 'pending-release' for lbl in pr.get('labels', [])):
                pending_release += 1

            if oldest_open_date is None or created_at < oldest_open_date:
                oldest_open_date = created_at

        total_changes_requested += len(prs_with_changes_requested)
        total_not_approved += len(prs_not_approved)
        total_hotfix += len(prs_hotfix)
        total_pending_24h += len(prs_pending_review)

        for pr in prs_merged_today:
            created = iso_to_dt(pr["created_at"])
            merged = iso_to_dt(pr["merged_at"])
            review_cycle_times.append((merged - created).total_seconds() / 3600)

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

        pr_ids_to_check = [pr["number"] for pr in prs_raised_today + prs_merged_today]
        reviews_done_map = defaultdict(int)
        for pr_id in pr_ids_to_check:
            reviews = get_reviews(owner, repo_name, pr_id)
            for r in reviews:
                reviewer = r["user"]["login"]
                reviews_done_map[reviewer] += 1
        for user, count_r in reviews_done_map.items():
            members[user]["reviews_done"] = count_r

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

    oldest_open = (now - oldest_open_date).days if oldest_open_date else 0
    avg_review_time = round(sum(review_cycle_times) / len(review_cycle_times), 2) if review_cycle_times else 0

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

    most_active = max(member_rows, key=lambda x: x["PRs Raised"])["Member"] if member_rows else "-"
    review_heavy = max(member_rows, key=lambda x: x["Reviews Done"])["Member"] if member_rows else "-"
    blocker_owners = "-"

    report_md = format_report_markdown(
        date_str=date_str,
        total_raised=total_raised,
        total_merged=total_merged,
        total_changes_requested=total_changes_requested,
        total_not_approved=total_not_approved,
        total_hotfix=total_hotfix,
        pending_24h=total_pending_24h,
        oldest_open=oldest_open,
        avg_review_time=avg_review_time,
        member_rows=member_rows,
        pr_stuck=pr_stuck,
        pending_release=pending_release,
        reopened_failed=reopened_failed,
        most_active=most_active,
        review_heavy=review_heavy,
        owners_stale_prs=owners_stale_prs,
        blocker_owners=blocker_owners
    )

    send_to_google_chat_via_webhook(report_md)

if __name__ == "__main__":
    main()
