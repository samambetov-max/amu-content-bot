"""Turns a short-lived Meta token from Graph API Explorer into a permanent Page token and finds the Instagram ID.

    python3 tools/meta_token.py APP_ID APP_SECRET SHORT_TOKEN

A Page token made from a long-lived user token does not expire, so the bot never needs a new one
(until you change the Facebook password or remove the app).
"""
import sys

import requests

GRAPH = "https://graph.facebook.com/v23.0"


def get(path: str, **params) -> dict:
    body = requests.get(f"{GRAPH}/{path}", params=params, timeout=30).json()
    if "error" in body:
        raise SystemExit(f"Ошибка Meta: {body['error'].get('message')}")
    return body


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    app_id, app_secret, short = sys.argv[1:]
    long_user = get("oauth/access_token", grant_type="fb_exchange_token", client_id=app_id,
                    client_secret=app_secret, fb_exchange_token=short)["access_token"]
    pages = get("me/accounts", access_token=long_user, fields="name,access_token,instagram_business_account").get("data", [])
    if not pages:
        raise SystemExit("Не найдено ни одной Facebook-страницы. Дайте приложению доступ к странице, связанной с Instagram.")
    for page in pages:
        ig = page.get("instagram_business_account", {}).get("id")
        print(f"\nСтраница: {page['name']}")
        if not ig:
            print("  Instagram к этой странице не привязан")
            continue
        print(f"  IG_USER_ID      = {ig}")
        print(f"  IG_ACCESS_TOKEN = {page['access_token']}")
    print("\nСкопируйте оба значения в GitHub → Settings → Secrets and variables → Actions.")


if __name__ == "__main__":
    main()
