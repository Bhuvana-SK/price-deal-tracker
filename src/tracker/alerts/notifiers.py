"""Notification channels: console (default), Discord webhook, Telegram bot."""

import sys
from typing import Protocol

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from tracker.alerts.models import Alert
from tracker.settings import Settings

_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=5),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    reraise=True,
)


class Notifier(Protocol):
    """Anything that can deliver an alert. `send` raises on failure."""

    name: str

    def send(self, alert: Alert) -> None: ...


class ConsoleNotifier:
    """Prints alerts to stdout. Used when no webhook is configured."""

    name = "console"

    def send(self, alert: Alert) -> None:
        """Print the alert."""
        print(f"\n🔔 {alert.text()}\n", file=sys.stdout, flush=True)


class DiscordNotifier:
    """Posts to a Discord channel webhook."""

    name = "discord"

    def __init__(self, webhook_url: str, client: httpx.Client | None = None) -> None:
        self._url = webhook_url
        self._client = client or httpx.Client(timeout=10)

    @_RETRY
    def send(self, alert: Alert) -> None:
        """POST the alert; Discord limits message content to 2000 characters."""
        resp = self._client.post(self._url, json={"content": alert.text()[:2000]})
        resp.raise_for_status()


class TelegramNotifier:
    """Sends a message through a Telegram bot."""

    name = "telegram"

    def __init__(self, token: str, chat_id: str, client: httpx.Client | None = None) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"  # never log this URL
        self._chat_id = chat_id
        self._client = client or httpx.Client(timeout=10)

    @_RETRY
    def send(self, alert: Alert) -> None:
        """POST the alert to the bot API."""
        payload = {"chat_id": self._chat_id, "text": alert.text()[:4096]}
        resp = self._client.post(self._url, json=payload)
        resp.raise_for_status()


def build_notifiers(settings: Settings) -> list[Notifier]:
    """Every configured channel, or the console if none is configured."""
    notifiers: list[Notifier] = []
    if settings.discord_webhook_url:
        notifiers.append(DiscordNotifier(settings.discord_webhook_url.get_secret_value()))
    if settings.telegram_bot_token and settings.telegram_chat_id:
        notifiers.append(
            TelegramNotifier(
                settings.telegram_bot_token.get_secret_value(), settings.telegram_chat_id
            )
        )
    return notifiers or [ConsoleNotifier()]
