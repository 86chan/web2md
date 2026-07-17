"""共通ユーティリティ(common モジュール)のテスト"""

from __future__ import annotations

from pathlib import Path

import pytest

from common import (
    SizeLimit,
    build_sections,
    count_words,
    generate_tree_str,
    is_text_link_suffix,
    parse_size,
    write_bundle,
)


@pytest.mark.parametrize(
    ("size_str", "expected_value"),
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
    size_str: str, expected_value: int
) -> None:
    """
    各種サイズ表記が SizeLimit(unit="bytes") へ正しく変換されることを検証する。

    Given: 単位付き/単位なし/小文字/空白付きのサイズ文字列。
    When: parse_size に渡す。
    Then: SizeLimit の value が期待するバイト数で、unit が "bytes" であること。
    """
    result = parse_size(size_str)
    assert result.value == expected_value
    assert result.unit == "bytes"


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


def test_parse_size_word_count_lower() -> None:
    """
    小文字 'w' サフィックスで単語数ベースの SizeLimit が返ることを検証する。

    Given: "50000w" のような単語数指定文字列。
    When: parse_size に渡す。
    Then: value=50000, unit="words", raw="50000w" の SizeLimit が返ること。
    """
    result = parse_size("50000w")
    assert result == SizeLimit(value=50000, unit="words", raw="50000w")


def test_parse_size_word_count_upper() -> None:
    """
    大文字 'W' サフィックスでも単語数ベースの SizeLimit が返ることを検証する。

    Given: "100000W" のような大文字サフィックス文字列。
    When: parse_size に渡す。
    Then: value=100000, unit="words" の SizeLimit が返ること。
    """
    result = parse_size("100000W")
    assert result.value == 100000
    assert result.unit == "words"


def test_parse_size_word_count_zero() -> None:
    """
    "0w" のエッジケースで value=0 の SizeLimit が返ることを検証する。

    Given: "0w" という最小値の単語数指定。
    When: parse_size に渡す。
    Then: value=0, unit="words" の SizeLimit が返ること。
    """
    result = parse_size("0w")
    assert result.value == 0
    assert result.unit == "words"


def test_count_words_basic() -> None:
    """
    空白区切りの単語数が正しくカウントされることを検証する。

    Given: 空白で区切られた単語列。
    When: count_words に渡す。
    Then: 単語数が返ること。
    """
    assert count_words("hello world foo bar") == 4
    assert count_words("") == 0
    assert count_words("single") == 1


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
    limit = SizeLimit(value=10_000, unit="bytes", raw="10KB")
    write_bundle(["hello", "world"], output, size_limit=limit)

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
    limit = SizeLimit(value=60, unit="bytes", raw="60B")
    write_bundle(sections, output, size_limit=limit)

    parts = sorted(tmp_path.glob("out_part_*.md"))
    assert len(parts) == 3
    # 先頭2パートには継続フッターが付与されること
    assert "Continued in:" in parts[0].read_text(encoding="utf-8")
    assert "C" * 50 in parts[2].read_text(encoding="utf-8")


def test_write_bundle_splits_by_word_count(tmp_path: Path) -> None:
    """
    単語数ベースで上限を超過した場合に分割されることを検証する。

    Given: 各セクションが 10 単語を持ち、上限を 15 単語に設定。
    When: write_bundle を呼ぶ。
    Then: 3セクションが複数パートに分割されること。
    """
    output = tmp_path / "out.md"
    # 各セクション 10 単語、上限 15 単語
    sections = [
        " ".join(f"word{i}" for i in range(10)),
        " ".join(f"alpha{i}" for i in range(10)),
        " ".join(f"beta{i}" for i in range(10)),
    ]
    limit = SizeLimit(value=15, unit="words", raw="15w")
    write_bundle(sections, output, size_limit=limit)

    parts = sorted(tmp_path.glob("out_part_*.md"))
    # 各セクションが 10 単語なので、フッターの単語数も加味して少なくとも 2 分割
    assert len(parts) >= 2


def test_write_bundle_no_split_by_word_count_within_limit(tmp_path: Path) -> None:
    """
    単語数ベースで上限以内の場合に分割されないことを検証する。

    Given: 各セクションが少数の単語を持ち、上限に余裕がある設定。
    When: write_bundle を呼ぶ。
    Then: 単一ファイルに出力され、分割ファイルが生成されないこと。
    """
    output = tmp_path / "out.md"
    sections = ["hello world", "foo bar"]
    limit = SizeLimit(value=1000, unit="words", raw="1000w")
    write_bundle(sections, output, size_limit=limit)

    assert output.exists()
    assert output.read_text(encoding="utf-8") == "hello worldfoo bar"
    assert list(tmp_path.glob("out_part_*.md")) == []


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
