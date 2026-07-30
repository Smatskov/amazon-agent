import asyncio
from pathlib import Path

import pytest

import amazon


REPRESENTATIVE_SEARCH_RESULT_HTML = """
<div class="s-result-item" data-asin="B0TEST">
  <a class="a-link-normal s-line-clamp-3" href="/dp/B0TEST"><span>Sensodyne Repair and Protect, 3 Pack</span></a>
  <span class="a-price"><span class="a-offscreen">$18.49</span><span class="a-offscreen">$1.23 per ounce</span></span>
  <span class="a-icon-alt">4.7 out of 5 stars</span>
  <span class="a-size-base s-underline-text">1,234</span>
  <i class="a-icon-prime"></i>
</div>
"""


def test_profile_directory_uses_configured_external_path(monkeypatch, tmp_path):
    profile = tmp_path / "amazon-profile"
    monkeypatch.setenv("AMAZON_BROWSER_PROFILE_DIR", str(profile))

    assert amazon.browser_profile_dir() == profile.resolve()


def test_profile_directory_rejects_repository_path(monkeypatch):
    monkeypatch.setenv(
        "AMAZON_BROWSER_PROFILE_DIR", str(amazon.REPOSITORY_ROOT / "amazon-profile")
    )

    with pytest.raises(amazon.AmazonProfileConfigurationError):
        amazon.browser_profile_dir()


def test_background_browser_is_default_and_visible_mode_is_explicit(monkeypatch):
    """A window must not pop open on every Telegram message."""
    monkeypatch.delenv("AMAZON_BROWSER_HEADLESS", raising=False)
    assert amazon._browser_headless() is True
    monkeypatch.setenv("AMAZON_BROWSER_HEADLESS", "false")
    assert amazon._browser_headless() is False


def test_manual_sign_in_is_always_visible_even_when_headless_is_configured(monkeypatch, tmp_path):
    """The sign-in script exists to be used by a human, so it ignores the setting."""
    monkeypatch.setenv("AMAZON_BROWSER_PROFILE_DIR", str(tmp_path / "profile"))
    monkeypatch.setenv("AMAZON_BROWSER_HEADLESS", "true")
    calls = {}

    class Context:
        pages = []

        async def new_page(self):
            raise AssertionError("the probe stops before navigation")

        async def close(self):
            pass

    class Chromium:
        async def launch_persistent_context(self, path, **kwargs):
            calls["headless"] = kwargs["headless"]
            return Context()

    class Playwright:
        chromium = Chromium()

    class Manager:
        async def __aenter__(self):
            return Playwright()

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(amazon, "async_playwright", lambda: Manager())

    async def open_and_close():
        async with amazon._persistent_browser_context(headless=False):
            pass

    asyncio.run(open_and_close())

    assert calls["headless"] is False


def test_persistent_context_uses_configured_profile_and_background_mode(monkeypatch, tmp_path):
    profile = tmp_path / "amazon-profile"
    monkeypatch.setenv("AMAZON_BROWSER_PROFILE_DIR", str(profile))
    monkeypatch.delenv("AMAZON_BROWSER_HEADLESS", raising=False)
    calls = {}

    class Context:
        async def close(self):
            calls["closed"] = True

    class Chromium:
        async def launch_persistent_context(self, path, **kwargs):
            calls["path"] = path
            calls["kwargs"] = kwargs
            return Context()

    class Playwright:
        chromium = Chromium()

    class Manager:
        async def __aenter__(self):
            return Playwright()

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(amazon, "async_playwright", lambda: Manager())

    async def open_and_close():
        async with amazon._persistent_browser_context():
            pass

    asyncio.run(open_and_close())

    assert calls["path"] == str(profile.resolve())
    assert calls["kwargs"]["headless"] is True
    assert calls["kwargs"]["viewport"] == {"width": 1440, "height": 1000}
    assert calls["closed"] is True


def test_persistent_context_does_not_hang_when_browser_close_times_out(monkeypatch, tmp_path, capsys):
    profile = tmp_path / "amazon-profile"
    monkeypatch.setenv("AMAZON_BROWSER_PROFILE_DIR", str(profile))
    monkeypatch.setattr(amazon, "BROWSER_CLOSE_TIMEOUT_SECONDS", 0.001)

    class Context:
        async def close(self):
            await asyncio.sleep(1)

    class Chromium:
        async def launch_persistent_context(self, *args, **kwargs):
            return Context()

    class Playwright:
        chromium = Chromium()

    class Manager:
        async def __aenter__(self):
            return Playwright()

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(amazon, "async_playwright", lambda: Manager())

    async def open_and_close():
        async with amazon._persistent_browser_context():
            pass

    asyncio.run(open_and_close())

    assert "browser context close timed out" in capsys.readouterr().out


def test_representative_amazon_search_html_extracts_only_visible_product_fields():
    """Covers the helper the live search path actually uses for card metadata."""
    price, rating, review_count, prime_eligible = amazon._result_metadata_from_html(
        REPRESENTATIVE_SEARCH_RESULT_HTML
    )

    assert price == "$18.49"
    assert rating == 4.7
    assert review_count == 1234
    assert prime_eligible is True


def test_variation_count_is_not_mistaken_for_a_price():
    """Variation listings put "2 scents" in the first offscreen span, before the price."""
    html = """
    <div data-asin="B0VAR">
      <a class="a-link-normal s-line-clamp-3" href="/dp/B0VAR"><span>Shampoo</span></a>
      <span class="a-offscreen">2 scents</span>
      <span class="a-price"><span class="a-offscreen">$12.47</span></span>
    </div>
    """

    price, _, _, _ = amazon._result_metadata_from_html(html)

    assert price == "$12.47"


def test_review_count_comes_from_the_accessibility_label():
    """The visible count is abbreviated ("(212.1K)"); the label carries the real number."""
    html = (
        '<div data-asin="B0R"><a class="a-link-normal s-line-clamp-3" href="/dp/B0R">'
        '<span>Item</span></a>'
        '<a aria-label="212,162 ratings" class="s-underline-text"><span>(212.1K)</span></a></div>'
    )

    _, _, review_count, _ = amazon._result_metadata_from_html(html)

    assert review_count == 212162


def test_prime_is_not_claimed_when_amazon_shows_a_join_prime_upsell():
    html = (
        '<div data-asin="B0P"><a class="a-link-normal s-line-clamp-3" href="/dp/B0P">'
        '<span>Item</span></a><i class="a-icon-prime"></i>'
        '<span class="prime-brand-color">Join Prime</span></div>'
    )

    _, _, _, prime = amazon._result_metadata_from_html(html)

    assert prime is None


def test_delivery_date_survives_the_tags_between_label_and_date():
    html = (
        '<div class="udm-secondary-delivery-message">Or Non-members get '
        '<span class="a-text-bold">FREE delivery</span> '
        '<span class="a-text-bold">Tue, Aug 4</span> on $35 of items</div>'
    )

    assert amazon._delivery_from_html(html) == "Tue, Aug 4"


def test_delivery_is_none_when_amazon_states_no_date():
    assert amazon._delivery_from_html("<div>Usually ships within a month</div>") is None


def test_result_metadata_reports_absent_fields_as_none_instead_of_guessing():
    html = '<div data-asin="B0BARE"><h2><a href="/dp/B0BARE">Plain Item</a></h2></div>'

    assert amazon._result_metadata_from_html(html) == (None, None, None, None)


def test_query_page_reuse_requires_exact_matching_amazon_search_query():
    assert amazon._query_matches_page(
        "https://www.amazon.com/s?k=Sensodyne+3+pack", "Sensodyne 3 pack"
    )
    assert not amazon._query_matches_page(
        "https://www.amazon.com/s?k=Sensodyne+3+pack", "AA batteries"
    )


def test_canonical_product_url_filter_rejects_advertising_redirects():
    assert amazon._is_amazon_product_url("https://www.amazon.com/dp/B0TEST")
    assert not amazon._is_amazon_product_url(
        "https://aax-us-east.amazon-adsystem.com/e/click"
    )


def test_current_product_link_selector_targets_amazon_result_titles_not_card_wrappers():
    assert "s-line-clamp-3" in amazon.PRODUCT_TITLE_SELECTOR
    assert "data-asin" not in amazon.PRODUCT_TITLE_SELECTOR
