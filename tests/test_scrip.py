"""クローラ(scrip モジュール)のテスト"""

from __future__ import annotations

from pathlib import Path

import pytest
import responses

import scrip
from common import SizeLimit
from scrip import CrawlConfig, rewrite_links_for_bundle, url_to_bundle_path


@pytest.mark.parametrize(
    ("url", "flat", "expected"),
    [
        ("https://ex.com/docs", False, "index.md"),
        ("https://ex.com/", False, "index.md"),
        ("https://ex.com/guide/intro.html", False, "guide/intro.md"),
        ("https://ex.com/guide/intro.html", True, "guide_intro.md"),
        ("https://ex.com/a/b/c", False, "a/b/c.md"),
    ],
)
def test_url_to_bundle_path(url: str, flat: bool, expected: str) -> None:
    """
    URL が成果物内の相対パスへ正しく変換されることを検証する。

    Given: ルート/拡張子付き/階層付きの各 URL と flat 指定。
    When: url_to_bundle_path に渡す。
    Then: 結合モード/フラットモードそれぞれの期待パスが返ること。
    """
    assert url_to_bundle_path(url, flat=flat) == expected


def test_rewrite_links_rewrites_internal_text_links() -> None:
    """
    クロール対象内のテキストリンクが成果物パスへ書き換えられることを検証する。

    Given: 内部テキストリンク・外部リンク・アンカー・画像リンクを含む HTML。
    When: rewrite_links_for_bundle を呼ぶ。
    Then: 内部テキストリンクのみ書き換えられ、他は元のまま残ること。
    """
    html = (
        '<a href="/docs/guide.html">internal</a>'
        '<a href="https://other.com/x">external</a>'
        '<a href="#section">anchor</a>'
        '<a href="/docs/image.png">image</a>'
    )
    result = rewrite_links_for_bundle(
        html, "https://ex.com/docs/", "https://ex.com/docs", flat=False
    )

    assert 'href="docs/guide.md"' in result
    assert 'href="https://other.com/x"' in result
    assert 'href="#section"' in result
    assert 'href="/docs/image.png"' in result


def _make_config(**overrides: object) -> CrawlConfig:
    """テスト用の CrawlConfig を既定値付きで生成するヘルパー。"""
    params: dict[str, object] = {
        "start_url": "https://ex.com/docs",
        "limit_prefix": "https://ex.com/docs",
        "output": "out.md",
        "size_limit": SizeLimit(value=10_000, unit="bytes", raw="10KB"),
        "as_html": False,
        "no_merge": False,
        "delay_min": 0.0,
        "delay_max": 0.0,
    }
    params.update(overrides)
    return CrawlConfig(**params)  # type: ignore[arg-type]


def test_next_delay_returns_value_within_range() -> None:
    """
    next_delay がランダム待機を下限〜上限の範囲で返すことを検証する。

    Given: 下限 0.5・上限 1.7 の設定。
    When: next_delay を多数回呼ぶ。
    Then: すべての戻り値が 0.5 以上 1.7 以下に収まること。
    """
    config = _make_config(delay_min=0.5, delay_max=1.7)
    samples = [config.next_delay() for _ in range(200)]
    assert all(0.5 <= value <= 1.7 for value in samples)
    # ランダム性により少なくとも2種類以上の値が出ること
    assert len({round(value, 4) for value in samples}) > 1


def test_next_delay_falls_back_to_min_when_max_not_greater() -> None:
    """
    上限が下限以下のとき next_delay が下限値を返すことを検証する。

    Given: 下限 0.3・上限 0.0 の設定。
    When: next_delay を呼ぶ。
    Then: 戻り値が下限 0.3 となること。
    """
    config = _make_config(delay_min=0.3, delay_max=0.0)
    assert config.next_delay() == 0.3


@responses.activate
def test_crawl_merges_pages_into_single_bundle(tmp_path: Path) -> None:
    """
    2ページを辿って単一の結合ファイルへ集約されることを検証する。

    Given: index から page1 へリンクする2ページのモックサイト。
    When: delay=0 で crawl を実行する。
    Then: 出力ファイルにツリーと両ページの本文が含まれること。
    """
    base = "https://ex.com/docs"
    responses.add(
        responses.GET,
        base,
        body=(
            '<html><main><h1>Index</h1><a href="/docs/page1.html">p1</a></main></html>'
        ),
        content_type="text/html",
    )
    responses.add(
        responses.GET,
        "https://ex.com/docs/page1.html",
        body="<html><main><h1>Page One</h1></main></html>",
        content_type="text/html",
    )

    output = tmp_path / "out.md"
    config = CrawlConfig(
        start_url=base,
        limit_prefix=base,
        output=str(output),
        size_limit=SizeLimit(value=10_000, unit="bytes", raw="10KB"),
        as_html=False,
        no_merge=False,
        delay_min=0.0,
        delay_max=0.0,
    )
    scrip.crawl(config)

    text = output.read_text(encoding="utf-8")
    assert "Directory structure:" in text
    assert "Index" in text
    assert "Page One" in text
    assert "File: docs/page1.md" in text


@responses.activate
def test_crawl_skips_404_without_retry(tmp_path: Path) -> None:
    """
    404 ページがリトライなしでスキップされることを検証する。

    Given: 404 を返す単一ページ。
    When: crawl を実行する。
    Then: リクエストは1回のみで、結合ファイルが空ツリーで生成されること。
    """
    base = "https://ex.com/missing"
    responses.add(responses.GET, base, status=404)

    output = tmp_path / "out.md"
    config = CrawlConfig(
        start_url=base,
        limit_prefix=base,
        output=str(output),
        size_limit=SizeLimit(value=10_000, unit="bytes", raw="10KB"),
        as_html=False,
        no_merge=False,
        delay_min=0.0,
        delay_max=0.0,
    )
    scrip.crawl(config)

    assert len(responses.calls) == 1
    assert output.exists()
