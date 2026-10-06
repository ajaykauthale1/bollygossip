"""Upload videos to YouTube from a metadata JSON (see output/uploads/*.json).

Needs client_secret.json (Google Cloud OAuth client, Desktop app, YouTube Data API v3)
next to this file. The first run opens a browser sign-in; pick the target channel.
Usage: python upload_youtube.py output/uploads/2026-10-05_upload_metadata.json
"""
import json
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

HERE = Path(__file__).parent
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]
CATEGORIES = {"Entertainment": "24", "News & Politics": "25", "People & Blogs": "22"}


def service():
    token = HERE / "youtube_token.json"
    creds = Credentials.from_authorized_user_file(token, SCOPES) if token.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds = InstalledAppFlow.from_client_secrets_file(HERE / "client_secret.json", SCOPES).run_local_server(port=0)
        token.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def main(meta_path):
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    yt = service()
    ch = yt.channels().list(part="snippet", mine=True).execute()["items"][0]["snippet"]["title"]
    print("Channel:", ch)
    if meta.get("channel") and meta["channel"].lower() not in ch.replace(" ", "").lower():
        sys.exit(f"Signed in to '{ch}', expected '{meta['channel']}'. Delete youtube_token.json and sign in again.")
    for v in meta["videos"]:
        body = {
            "snippet": {"title": v["title"], "description": v["description"], "tags": v["tags"],
                        "categoryId": CATEGORIES.get(v.get("category"), "24")},
            "status": {"privacyStatus": meta.get("privacy", "unlisted"), "selfDeclaredMadeForKids": False},
        }
        if v.get("language"):
            body["snippet"]["defaultAudioLanguage"] = v["language"]
        req = yt.videos().insert(part="snippet,status", body=body,
                                 media_body=MediaFileUpload(v["file"], chunksize=16 * 1024 * 1024, resumable=True))
        resp = None
        while resp is None:
            status, resp = req.next_chunk()
            if status:
                print(f"  {Path(v['file']).name}: {int(status.progress() * 100)}%", flush=True)
        print(f"{v['title']}\n  https://youtu.be/{resp['id']}  ({resp['status']['privacyStatus']})", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
