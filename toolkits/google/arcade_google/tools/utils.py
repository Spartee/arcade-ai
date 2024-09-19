from base64 import urlsafe_b64decode
import datetime
from enum import Enum
import re
from typing import Any, Dict, Optional

from bs4 import BeautifulSoup


class DateRange(Enum):
    TODAY = "today"
    YESTERDAY = "yesterday"
    LAST_7_DAYS = "last_7_days"
    LAST_30_DAYS = "last_30_days"
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    THIS_YEAR = "this_year"

    def to_date_query(self):
        today = datetime.datetime.now()
        result = "after:"
        comparison_date = today

        if self == DateRange.YESTERDAY:
            comparison_date = today - datetime.timedelta(days=1)
        elif self == DateRange.LAST_7_DAYS:
            comparison_date = today - datetime.timedelta(days=7)
        elif self == DateRange.LAST_30_DAYS:
            comparison_date = today - datetime.timedelta(days=30)
        elif self == DateRange.THIS_MONTH:
            comparison_date = today.replace(day=1)
        elif self == DateRange.LAST_MONTH:
            comparison_date = (
                today.replace(day=1) - datetime.timedelta(days=1)
            ).replace(day=1)
        elif self == DateRange.THIS_YEAR:
            comparison_date = today.replace(month=1, day=1)
        elif self == DateRange.LAST_MONTH:
            comparison_date = (
                today.replace(month=1, day=1) - datetime.timedelta(days=1)
            ).replace(month=1, day=1)

        return result + comparison_date.strftime("%Y/%m/%d")


def parse_email(email_data: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Parse email data and extract relevant information.

    Args:
        email_data (Dict[str, Any]): Raw email data from Gmail API.

    Returns:
        Optional[Dict[str, str]]: Parsed email details or None if parsing fails.
    """
    try:
        payload = email_data["payload"]
        headers = {d["name"].lower(): d["value"] for d in payload["headers"]}

        body_data = _get_email_body(payload)

        return {
            "id": email_data.get("id", ""),
            "from": headers.get("from", ""),
            "date": headers.get("date", ""),
            "subject": headers.get("subject", "No subject"),
            "body": _clean_email_body(body_data) if body_data else "",
        }
    except Exception as e:
        print(f"Error parsing email {email_data.get('id', 'unknown')}: {e}")
        return None


def parse_draft_email(draft_email_data: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Parse draft email data and extract relevant information.

    Args:
        draft_email_data (Dict[str, Any]): Raw draft email data from Gmail API.

    Returns:
        Optional[Dict[str, str]]: Parsed draft email details or None if parsing fails.
    """
    try:
        message = draft_email_data["message"]
        payload = message["payload"]
        headers = {d["name"].lower(): d["value"] for d in payload["headers"]}

        body_data = _get_email_body(payload)

        return {
            "id": draft_email_data.get("id", ""),
            "from": headers.get("from", ""),
            "date": headers.get("internaldate", ""),
            "subject": headers.get("subject", "No subject"),
            "body": _clean_email_body(body_data) if body_data else "",
        }
    except Exception as e:
        print(f"Error parsing draft email {draft_email_data.get('id', 'unknown')}: {e}")
        return None


def get_draft_url(draft_id):
    return f"https://mail.google.com/mail/u/0/#drafts/{draft_id}"


def get_sent_email_url(sent_email_id):
    return f"https://mail.google.com/mail/u/0/#sent/{sent_email_id}"


def get_email_in_trash_url(email_id):
    return f"https://mail.google.com/mail/u/0/#trash/{email_id}"


def _get_email_body(payload: Dict[str, Any]) -> Optional[str]:
    """
    Extract email body from payload.

    Args:
        payload (Dict[str, Any]): Email payload data.

    Returns:
        Optional[str]: Decoded email body or None if not found.
    """
    if "body" in payload and payload["body"].get("data"):
        return urlsafe_b64decode(payload["body"]["data"]).decode()

    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and "data" in part["body"]:
            return urlsafe_b64decode(part["body"]["data"]).decode()

    return None


def _clean_email_body(body: str) -> str:
    """
    Remove HTML tags and clean up email body text while preserving most content.

    Args:
        body (str): The raw email body text.

    Returns:
        str: Cleaned email body text.
    """
    try:
        # Remove HTML tags using BeautifulSoup
        soup = BeautifulSoup(body, "html.parser")
        text = soup.get_text(separator=" ")

        # Clean up the text
        text = _clean_text(text)

        return text.strip()
    except Exception as e:
        print(f"Error cleaning email body: {e}")
        return body


def _clean_text(text: str) -> str:
    """
    Clean up the text while preserving most content.

    Args:
        text (str): The input text.

    Returns:
        str: Cleaned text.
    """
    # Replace multiple newlines with a single newline
    text = re.sub(r"\n+", "\n", text)

    # Replace multiple spaces with a single space
    text = re.sub(r"\s+", " ", text)

    # Remove leading/trailing whitespace from each line
    text = "\n".join(line.strip() for line in text.split("\n"))

    return text