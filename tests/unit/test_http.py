import httpx
import pytest
import respx

from tracker.sources.http import PoliteClient, RetryableHTTPError, RobotsDisallowedError

SITE = "https://shop.test"


@respx.mock
def test_robots_disallow_blocks_request(fast_client: PoliteClient) -> None:
    respx.get(f"{SITE}/robots.txt").respond(200, text="User-agent: *\nDisallow: /private\n")
    page = respx.get(f"{SITE}/private/item").respond(200)
    with pytest.raises(RobotsDisallowedError):
        fast_client.get(f"{SITE}/private/item")
    assert not page.called


@respx.mock
def test_missing_robots_allows_everything(fast_client: PoliteClient) -> None:
    respx.get(f"{SITE}/robots.txt").respond(404)
    respx.get(f"{SITE}/item").respond(200, text="ok")
    assert fast_client.get(f"{SITE}/item").text == "ok"


@respx.mock
def test_robots_server_error_blocks_everything(fast_client: PoliteClient) -> None:
    respx.get(f"{SITE}/robots.txt").respond(503)
    with pytest.raises(RobotsDisallowedError):
        fast_client.get(f"{SITE}/item")


@respx.mock
def test_retries_with_backoff_then_succeeds(fast_client: PoliteClient, sleeps: list[float]) -> None:
    respx.get(f"{SITE}/robots.txt").respond(404)
    route = respx.get(f"{SITE}/item").mock(
        side_effect=[httpx.Response(503), httpx.ConnectError("boom"), httpx.Response(200)]
    )
    assert fast_client.get(f"{SITE}/item").status_code == 200
    assert route.call_count == 3
    assert len(sleeps) >= 2  # backoff waits happened (via the fake sleep)


@respx.mock
def test_gives_up_after_max_attempts(fast_client: PoliteClient) -> None:
    respx.get(f"{SITE}/robots.txt").respond(404)
    route = respx.get(f"{SITE}/item").respond(429)
    with pytest.raises(RetryableHTTPError):
        fast_client.get(f"{SITE}/item")
    assert route.call_count == 3


@respx.mock
def test_client_errors_are_not_retried(fast_client: PoliteClient) -> None:
    respx.get(f"{SITE}/robots.txt").respond(404)
    route = respx.get(f"{SITE}/item").respond(404)
    with pytest.raises(httpx.HTTPStatusError):
        fast_client.get(f"{SITE}/item")
    assert route.call_count == 1


@respx.mock
def test_rate_limit_spaces_requests(fast_client: PoliteClient, sleeps: list[float]) -> None:
    respx.get(f"{SITE}/robots.txt").respond(404)
    respx.get(url__startswith=f"{SITE}/item").respond(200)
    fast_client.get(f"{SITE}/item/1")
    fast_client.get(f"{SITE}/item/2")
    assert sleeps and 0 < sleeps[-1] <= 0.5
