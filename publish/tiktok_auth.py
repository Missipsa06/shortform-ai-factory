"""
Authentification TikTok OAuth2 — ouvre le navigateur et échange le code manuellement.

Usage :
    python publish/tiktok_auth.py

Prérequis dans .env :
    TIKTOK_CLIENT_KEY=...
    TIKTOK_CLIENT_SECRET=...

Dans TikTok Developer → ton app → Redirect URI :
    https://localhost
"""

import os
import secrets
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv, set_key

load_dotenv()

REDIRECT_URI = "https://localhost"
SCOPES       = "video.publish,video.upload"
ENV_PATH     = Path(".env")


def main() -> None:
    client_key    = os.getenv("TIKTOK_CLIENT_KEY", "")
    client_secret = os.getenv("TIKTOK_CLIENT_SECRET", "")

    if not client_key or not client_secret:
        print("Ajoute dans .env :")
        print("  TIKTOK_CLIENT_KEY=...")
        print("  TIKTOK_CLIENT_SECRET=...")
        return

    state    = secrets.token_urlsafe(16)
    auth_url = (
        "https://www.tiktok.com/v2/auth/authorize/?"
        + urlencode({
            "client_key":    client_key,
            "scope":         SCOPES,
            "response_type": "code",
            "redirect_uri":  REDIRECT_URI,
            "state":         state,
        })
    )

    print("Ouverture du navigateur TikTok...")
    webbrowser.open(auth_url)
    print()
    print("1. Connecte-toi et autorise l'app")
    print("2. Le navigateur affiche une erreur (normal pour https://localhost)")
    print("3. Copie l'URL complète de la barre d'adresse")
    print("   Elle ressemble à : https://localhost?code=XXXX&state=...")
    print()

    raw_url = input("Colle l'URL ici : ").strip()

    params = parse_qs(urlparse(raw_url).query)
    code   = params.get("code", [None])[0]

    if not code:
        print("Code introuvable dans l'URL. Réessaie.")
        return

    print("Échange du code contre un token...")
    resp = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key":    client_key,
            "client_secret": client_secret,
            "code":          code,
            "grant_type":    "authorization_code",
            "redirect_uri":  REDIRECT_URI,
        },
        timeout=30,
    )
    data = resp.json()

    if "access_token" not in data:
        print(f"Erreur : {data}")
        return

    ENV_PATH.touch(exist_ok=True)
    set_key(str(ENV_PATH), "TIKTOK_ACCESS_TOKEN",  data["access_token"])
    set_key(str(ENV_PATH), "TIKTOK_REFRESH_TOKEN", data.get("refresh_token", ""))

    print(f"\nToken sauvegardé dans .env")
    print(f"  Expire dans : {data.get('expires_in', '?')}s (~24h)")
    print("\nLance maintenant :")
    print("  python -m publish.tiktok --video data/videos/... --title '...'")


if __name__ == "__main__":
    main()
