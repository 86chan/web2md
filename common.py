"""
web2md 共通ユーティリティ

scrip.py と merge.py が共有するサイズ解析・ツリー生成・成果物書き出しの集約モジュール
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("web2md")

# リンクとして辿る（＝成果物に含まれうる）テキスト系の拡張子
ALLOWED_LINK_EXTENSIONS: frozenset[str] = frozenset(
    {
        "",
        ".html",
        ".htm",
        ".txt",
        ".md",
        ".rst",
        ".xml",
        ".json",
        ".php",
        ".jsp",
        ".asp",
        ".aspx",
        ".shtml",
        ".cgi",
    }
)

# サイズ単位（接尾辞）と乗数の対応。長い接尾辞から順に判定するため tuple で保持
_SIZE_UNITS: tuple[tuple[str, int], ...] = (
    ("KB", 1024),
    ("MB", 1024**2),
    ("GB", 1024**3),
    ("B", 1),
)

# 成果物のファイル区切りに用いる罫線
_SECTION_RULE: str = "=" * 48

# ディレクトリ階層を表現する再帰的なツリー辞書型
TreeNode = dict[str, "TreeNode"]


@dataclass(frozen=True)
class SizeLimit:
    """
    サイズ制限の統一表現

    バイト数ベースと単語数ベースの制限を同一の型で扱うための不変データ構造

    Attributes:
        value (int): 制限値（バイト数または単語数）
        unit (str): 単位（"bytes" または "words"）
        raw (str): 元表記（ログ表示用）
    """

    value: int
    unit: str
    raw: str


def parse_size(size_str: str) -> SizeLimit:
    """
    人間可読サイズ表記の SizeLimit 変換

    `500KB`・`1.5MB`・`200B`・単位なし数値に加え、`50000w` のような単語数指定にも対応

    Args:
        size_str (str): サイズ表記文字列

    Returns:
        SizeLimit: 制限値・単位・元表記を保持する不変構造体

    Raises:
        ValueError: 解釈不能な表記時
    """
    normalized = size_str.strip().upper()
    try:
        # 'w' サフィックスは単語数ベースの制限として扱う
        if normalized.endswith("W"):
            word_count = int(normalized[:-1].strip())
            return SizeLimit(value=word_count, unit="words", raw=size_str.strip())
        # 長い接尾辞を優先（"KB" を "B" より先に判定）
        for suffix, multiplier in sorted(
            _SIZE_UNITS, key=lambda unit: len(unit[0]), reverse=True
        ):
            if normalized.endswith(suffix):
                number = float(normalized[: -len(suffix)].strip())
                return SizeLimit(
                    value=int(number * multiplier),
                    unit="bytes",
                    raw=size_str.strip(),
                )
        # 接尾辞なしはバイト数の生値として扱う
        return SizeLimit(value=int(normalized), unit="bytes", raw=size_str.strip())
    except ValueError as error:
        raise ValueError(
            f"無効なサイズ指定です: '{size_str}'。"
            "数値または単位付きの文字列（例: 200B, 500KB, 1.5MB, 50000w）を"
            "指定してください。"
        ) from error


def count_words(text: str) -> int:
    """
    空白区切りによる単語数の算出

    LLM トークナイザに近い粗い近似として空白分割を採用

    Args:
        text (str): 対象文字列

    Returns:
        int: 単語数
    """
    return len(text.split())


def generate_tree_str(paths: Iterable[str]) -> str:
    """
    パス一覧からの Gitingest 形式ツリー文字列生成

    Args:
        paths (Iterable[str]): スラッシュ区切りの相対パス群

    Returns:
        str: ルート `.` から始まる罫線付きツリー
    """
    # ネストした辞書でディレクトリ階層を表現する
    tree: TreeNode = {}
    for path in paths:
        current = tree
        for part in path.split("/"):
            current = current.setdefault(part, {})

    def format_tree(node: TreeNode, prefix: str = "") -> list[str]:
        """
        ツリー辞書の罫線付き行リスト変換

        Args:
            node (TreeNode): 階層辞書
            prefix (str): 親階層から継承する罫線接頭辞

        Returns:
            list[str]: 整形済みの行リスト
        """
        lines: list[str] = []
        # 末尾要素のみ "└── "、それ以外は "├── "
        pointers = ["├── "] * (len(node) - 1) + ["└── "] if node else []
        for pointer, (key, child) in zip(pointers, sorted(node.items()), strict=True):
            lines.append(prefix + pointer + key)
            if child:
                # 親が分岐中なら縦線を、末尾なら空白を子へ引き継ぐ
                extension = "│   " if pointer == "├── " else "    "
                lines.extend(format_tree(child, prefix + extension))
        return lines

    return "\n".join(["."] + format_tree(tree))


def build_sections(ingested_data: Mapping[str, str]) -> list[str]:
    """
    収集データからの成果物セクション生成

    先頭にディレクトリツリー、以降に `File:` 見出し付きの各ファイル本文を配置

    Args:
        ingested_data (Mapping[str, str]): ファイルパスと本文の対応

    Returns:
        list[str]: ツリーヘッダと各ファイルセクションの並び
    """
    sections: list[str] = []

    # 1. ディレクトリツリーを最初のセクションとする
    tree_header = (
        "Directory structure:\n" + generate_tree_str(ingested_data.keys()) + "\n\n"
    )
    sections.append(tree_header)

    # 2. 各ファイルの内容をセパレーター付きで追加（パス順で安定化）
    for path, content in sorted(ingested_data.items()):
        clean_content = content.replace("\r\n", "\n")
        section = f"{_SECTION_RULE}\nFile: {path}\n{_SECTION_RULE}\n{clean_content}\n\n"
        sections.append(section)

    return sections


def _byte_length(text: str) -> int:
    """
    UTF-8 エンコード時のバイト長算出

    Args:
        text (str): 対象文字列

    Returns:
        int: UTF-8 バイト数
    """
    return len(text.encode("utf-8"))


def _measure(text: str, size_limit: SizeLimit) -> int:
    """
    SizeLimit の単位に応じたテキスト計測

    バイト数モードでは UTF-8 エンコード長、単語数モードでは空白分割数を返す

    Args:
        text (str): 計測対象文字列
        size_limit (SizeLimit): 計測基準となるサイズ制限

    Returns:
        int: 計測結果（バイト数または単語数）
    """
    # 単位に応じて計測関数を切り替える
    if size_limit.unit == "words":
        return count_words(text)
    return _byte_length(text)


def write_bundle(
    sections: list[str],
    output_file: str | Path,
    size_limit: SizeLimit,
) -> None:
    """
    成果物のサイズ制限付き書き出し

    総量が上限以内なら単一ファイル、超過時は `_part_N` 連番へ分割
    バイト数モードと単語数モードの両方に対応

    Args:
        sections (list[str]): 書き出し対象セクション列
        output_file (str | Path): 出力ファイルパス
        size_limit (SizeLimit): サイズ制限（値・単位・元表記を保持）
    """
    out_path = Path(output_file)
    out_dir = out_path.parent
    if out_dir != Path("."):
        out_dir.mkdir(parents=True, exist_ok=True)

    total_measure = sum(_measure(section, size_limit) for section in sections)

    # 単位に応じたログメッセージの切り替え
    unit_label = "words" if size_limit.unit == "words" else "バイト"

    if total_measure <= size_limit.value:
        # 分割不要時は指定ファイル名へそのまま出力
        body = "".join(sections).lstrip("\ufeff")
        out_path.write_text(body, encoding="utf-8", newline="\n")
        if size_limit.unit == "words":
            logger.info(
                "'%s' に出力しました (総ワード数: %d words)",
                out_path,
                total_measure,
            )
        else:
            logger.info(
                "'%s' に出力しました (総サイズ: %d バイト)",
                out_path,
                total_measure,
            )
        return

    stem = out_path.stem
    suffix = out_path.suffix
    file_idx = 1
    current_content = ""

    for section in sections:
        section_measure = _measure(section, size_limit)
        next_filename = f"{stem}_part_{file_idx + 1:04d}{suffix}"
        footer = (
            f"\n\n>>> NOTE: This file has been split. Continued in: {next_filename}\n"
        )
        current_measure = _measure(current_content, size_limit)
        footer_measure = _measure(footer, size_limit)

        # 既存内容にこのセクションを足すと上限超過する場合は現パートを確定
        if (
            current_measure > 0
            and (current_measure + section_measure + footer_measure) > size_limit.value
        ):
            part_path = out_dir / f"{stem}_part_{file_idx:04d}{suffix}"
            part_path.write_text(
                (current_content + footer).lstrip("\ufeff"),
                encoding="utf-8",
                newline="\n",
            )
            logger.info(
                "Created: %s (サイズ: %d %s)",
                part_path,
                current_measure + footer_measure,
                unit_label,
            )
            current_content = section
            file_idx += 1
        else:
            current_content += section

    # 残余コンテンツを最終パートとして書き出し（フッターなし）
    if current_content:
        part_path = out_dir / f"{stem}_part_{file_idx:04d}{suffix}"
        part_path.write_text(
            current_content.lstrip("\ufeff"), encoding="utf-8", newline="\n"
        )
        logger.info(
            "Created: %s (サイズ: %d %s)",
            part_path,
            _measure(current_content, size_limit),
            unit_label,
        )

    logger.info(
        "ファイルサイズ超過のため %d 個に分割して出力しました (制限上限: %s)",
        file_idx,
        size_limit.raw,
    )


def is_text_link_suffix(suffix: str) -> bool:
    """
    テキスト系リンク拡張子の判定

    既知のテキスト拡張子、または `.123` 形式の数値拡張子を許可

    Args:
        suffix (str): 小文字化済みのファイル拡張子

    Returns:
        bool: テキストとして辿る対象か
    """
    # ドット＋数値（例: .1, .123）も暗黙的に HTML（テキスト）として許可する
    is_numeric_suffix = bool(re.match(r"^\.[0-9]+$", suffix))
    return suffix in ALLOWED_LINK_EXTENSIONS or is_numeric_suffix
