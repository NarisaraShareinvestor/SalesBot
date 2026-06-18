"""Microsoft Teams / Azure AD OAuth + Microsoft Graph integration."""
import os
import json
from datetime import datetime, timedelta
from typing import Optional

import requests
from sqlalchemy.orm import Session

from models import UserCredential

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# Azure AD config
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "common")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://salesbot.ohmai.me/auth/microsoft/callback")


SCOPE = "User.Read Calendars.ReadWrite ChatMessage.Send offline_access openid profile email"


def get_oauth_url() -> str:
    """Generate Azure AD OAuth login URL."""
    scope = SCOPE
    auth_url = (
        f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/authorize"
        f"?client_id={AZURE_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={scope}"
        f"&response_type=code"
        f"&response_mode=query"
    )
    return auth_url


async def exchange_code_for_token(code: str) -> Optional[dict]:
    """Exchange auth code for access token."""
    token_url = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": AZURE_CLIENT_ID,
        "scope": SCOPE,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
        "client_secret": AZURE_CLIENT_SECRET,
    }
    try:
        r = requests.post(token_url, data=data, timeout=10)
        if r.status_code == 200:
            return r.json()
        print(f"Token exchange failed: HTTP {r.status_code} {r.text}")
    except Exception as e:
        print(f"Token exchange error: {e}")
    return None


async def get_user_info(access_token: str) -> Optional[dict]:
    """Get user info from Microsoft Graph."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        r = requests.get(f"{GRAPH_API_BASE}/me", headers=headers, timeout=10)
        if r.status_code == 200:
            return r.json()
        print(f"Get user info failed: HTTP {r.status_code} {r.text}")
    except Exception as e:
        print(f"Get user info error: {e}")
    return None


def store_credential(db: Session, email: str, name: str, token_response: dict):
    """Store or update user credential."""
    cred = db.query(UserCredential).filter(UserCredential.email == email).first()
    expires_at = None
    if "expires_in" in token_response:
        expires_at = datetime.utcnow() + timedelta(seconds=token_response["expires_in"])

    if cred:
        cred.access_token = token_response.get("access_token", "")
        cred.refresh_token = token_response.get("refresh_token", cred.refresh_token)
        cred.token_expires_at = expires_at
        cred.updated_at = datetime.utcnow()
    else:
        cred = UserCredential(
            email=email,
            name=name,
            access_token=token_response.get("access_token", ""),
            refresh_token=token_response.get("refresh_token"),
            token_expires_at=expires_at,
        )
        db.add(cred)
    db.commit()
    db.refresh(cred)
    return cred


def get_credential(db: Session, email: str) -> Optional[UserCredential]:
    """Get user credential."""
    return db.query(UserCredential).filter(UserCredential.email == email).first()


async def create_calendar_event(
    access_token: str,
    title: str,
    start_dt: datetime,
    end_dt: datetime,
    description: str = "",
) -> bool:
    """Create event in user's Teams calendar."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    body = {
        "subject": title,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "UTC"},
        "bodyPreview": description,
        "body": {"contentType": "HTML", "content": description},
    }
    try:
        r = requests.post(
            f"{GRAPH_API_BASE}/me/events",
            headers=headers,
            json=body,
            timeout=10,
        )
        return r.status_code in (200, 201)
    except Exception as e:
        print(f"Create calendar event error: {e}")
    return False


async def send_teams_chat_message(
    access_token: str, chat_id: str, message: str
) -> bool:
    """Send adaptive card message to Teams chat."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    # Adaptive card format
    body = {
        "body": [
            {"type": "TextBlock", "text": message, "wrap": True, "weight": "bolder"}
        ],
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
    }
    payload = {"contentType": "application/vnd.microsoft.card.adaptive", "content": body}
    try:
        r = requests.post(
            f"{GRAPH_API_BASE}/chats/{chat_id}/messages",
            headers=headers,
            json=payload,
            timeout=10,
        )
        return r.status_code in (200, 201)
    except Exception as e:
        print(f"Send Teams message error: {e}")
    return False
