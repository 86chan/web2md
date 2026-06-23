"""共通ユーティリティ(common モジュール)のテスト"""

from __future__ import annotations

from pathlib import Path

import pytest

from common import (
    build_sections,
    generate_tree_str,
    is_text_link_suffix,
    parse_size,
    write_bundle,
)


@pytest.mark.parametrize(
    ("size_str", "expected"),
    [
        ("1024", 1024),
        ("200B", 200),
        ("1KB", 1024),
        ("500KB", 500 * 1024),
        ("1.5MB", int(1.5 * 1024 * 1024)),
        ("1GB", 1024**3),
        ("  2mb  ", 2 * 1024 * 1024),
    ],
)
def test_parse_size_accepts_units_and_plain_numbers(
    size_str: str, expected: int
) -> None:
    """
    各種サイズ表記がバイト数へ正しく変換されることを検証する。

    Given: 単位付き/単位なし/小文字/空白付きのサイズ文字列。
    When: parse_size に渡す。
    Then: 期待するバイト数(int)が返ること。
    """
    assert parse_size(size_str) == expected


@pytest.mark.parametrize("invalid", ["abc", "10XB", "", "MB"])
def test_parse_size_rejects_invalid_strings(invalid: str) -> None:
    """
    不正なサイズ表記で ValueError が送出されることを検証する。

    Given: 数値として解釈できないサイズ文字列。
    When: parse_size に渡す。
    Then: ValueError が送出されること。
    """
    with pytest.raises(ValueError):
        parse_size(invalid)


def test_generate_tree_str_builds_nested_structure() -> None:
    """
    階層パス群から罫線付きツリー文字列が生成されることを検証する。

    Given: ネストしたパスとトップレベルのパス。
    When: generate_tree_str に渡す。
    Then: ルート '.' から始まり、全要素が罫線付きで含まれること。
    """
    paths = ["index.md", "folder/sub/file.md", "folder/other.md"]
    tree = generate_tree_str(paths)

    assert tree.startswith(".")
    assert "index.md" in tree
    assert "folder" in tree
    assert "file.md" in tree
    assert "└── " in tree


def test_build_sections_places_tree_first_and_file_headers() -> None:
    """
    成果物セクションが「ツリー先頭 + File 見出し」で構成されることを検証する。

    Given: 2件のファイルパスと本文の対応。
    When: build_sections に渡す。
    Then: 先頭がツリーヘッダ、続く各セクションに File 見出しと本文が含まれること。
    """
    data = {"a.md": "AAA", "b.md": "BBB"}
    sections = build_sections(data)

    assert sections[0].startswith("Directory structure:")
    body = "".join(sections)
    assert "File: a.md" in body
    assert "File: b.md" in body
    assert "AAA" in body and "BBB" in body


def test_build_sections_normalizes_crlf() -> None:
    """
    本文中の CRLF が LF に正規化されることを検証する。

    Given: CRLF を含む本文。
    When: build_sections に渡す。
    Then: 生成セクションに CRLF が残らないこと。
    """
    sections = build_sections({"a.md": "line1\r\nline2"})
    assert "\r\n" not in "".join(sections)


def test_write_bundle_writes_single_file_when_within_limit(tmp_path: Path) -> None:
    """
    総サイズが上限以内のとき単一ファイルへ出力されることを検証する。

    Given: 小さなセクション列と十分大きな上限。
    When: write_bundle を呼ぶ。
    Then: 指定ファイルが1つだけ作成され、内容が書き込まれること。
    """
    output = tmp_path / "out.md"
    write_bundle(["hello", "world"], output, max_size=10_000, max_size_str="10KB")

    assert output.exists()
    assert output.read_text(encoding="utf-8") == "helloworld"
    # 分割ファイルが作られていないこと
    assert list(tmp_path.glob("out_part_*.md")) == []


def test_write_bundle_splits_when_exceeding_limit(tmp_path: Path) -> None:
    """
    総サイズが上限超過のとき連番ファイルへ分割されることを検証する。

    Given: 各々が上限に迫る複数セクションと小さな上限。
    When: write_bundle を呼ぶ。
    Then: out_part_1.md / out_part_2.md ... が生成され、各内容が分散すること。
    """
    output = tmp_path / "out.md"
    # 各セクション 50 バイト、上限 60 バイトなら 1 セクションずつ分割される
    sections = ["A" * 50, "B" * 50, "C" * 50]
    write_bundle(sections, output, max_size=60, max_size_str="60B")

    parts = sorted(tmp_path.glob("out_part_*.md"))
    assert len(parts) == 3
    # 先頭2パートには継続フッターが付与されること
    assert "Continued in:" in parts[0].read_text(encoding="utf-8")
    assert "C" * 50 in parts[2].read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        ("", True),
        (".html", True),
        (".md", True),
        (".123", True),
        (".png", False),
        (".pdf", False),
        (".zip", False),
    ],
)
def test_is_text_link_suffix(suffix: str, expected: bool) -> None:
    """
    拡張子がテキスト系リンク対象か正しく判定されることを検証する。

    Given: テキスト系/数値/非テキストの各拡張子。
    When: is_text_link_suffix に渡す。
    Then: テキスト系と数値拡張子のみ True となること。
    """
    assert is_text_link_suffix(suffix) is expected
