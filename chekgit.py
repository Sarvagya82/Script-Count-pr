import requests
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("GITHUB_TOKEN")
OWNER = os.getenv("REPO_OWNER")
REPO = os.getenv("REPO_NAME")

headers = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github+json"
}

url = f"https://api.github.com/repos/{OWNER}/{REPO}/pulls?state=open"

resp = requests.get(url, headers=headers)
print(resp.status_code)
print(resp.text)
