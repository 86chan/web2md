"""
ローカルディレクトリ集約ツール

指定ディレクトリ内のテキスト/HTML を収集し、HTML は Markdown 変換のうえ
gitingest 形式の単一（または分割）ファイルへ集約する CLI
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterable
from pathlib import Path

import mdformat
import trafilatura
from bs4 import BeautifulSoup

from common import build_sections, parse_size, write_bundle

logger = logging.getLogger("web2md")

# 既定で集約対象とするテキスト系拡張子
DEFAULT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".txt",
        ".py",
        ".md",
        ".csv",
        ".json",
        ".html",
        ".htm",
        ".js",
        ".ts",
        ".css",
        ".sh",
        ".yaml",
        ".yml",
    }
)


def _format_markdown(raw_markdown: str) -> str:
    """
    mdformat による Markdown 整形

    整形失敗時は元テキストへフォールバックし、握りつぶさず警告記録

    Args:
        raw_markdown (str): 整形前 Markdown

    Returns:
        str: 整形済み（または整形前）Markdown
    """
    try:
        formatted: str = mdformat.text(raw_markdown)
        return formatted
    except (ValueError, RuntimeError) as error:
        # 整形不能でも処理は継続するが、原因を握りつぶさず記録する
        logger.warning("mdformat による整形に失敗しました: %s", error)
        return raw_markdown


def _html_to_markdown(html_content: str) -> str:
    """
    HTML から Markdown への高精度変換

    trafilatura による本文抽出、抽出不能時は BeautifulSoup でテキスト化

    Args:
        html_content (str): HTML 文字列

    Returns:
        str: 整形済み Markdown
    """
    # trafilatura で高精度な本文抽出と Markdown 変換を実行
    raw_markdown = trafilatura.extract(
        html_content,
        output_format="markdown",
        include_links=True,
        include_images=False,
    )

    # 何も抽出されなかった場合のフォールバック（BeautifulSoup）
    if not raw_markdown:
        soup = BeautifulSoup(html_content, "html.parser")
        raw_markdown = soup.get_text(separator="\n", strip=True)

    return _format_markdown(raw_markdown)


def _iter_target_files(
    target_path: Path, extensions: frozenset[str], output_path: Path
) -> Iterable[Path]:
    """
    集約対象ファイルの逐次列挙

    ドットファイル・__pycache__・node_modules・出力先自身を除外

    Args:
        target_path (Path): 走査対象ディレクトリ
        extensions (frozenset[str]): 対象拡張子集合
        output_path (Path): 自己集約回避用の出力先パス

    Yields:
        Path: 集約対象ファイルパス
    """
    resolved_output = output_path.resolve()
    for filepath in sorted(target_path.rglob("*")):
        if filepath.is_dir():
            continue
        # 隠しファイル/ディレクトリ配下は除外
        if any(part.startswith(".") for part in filepath.parts):
            continue
        if filepath.name == "__pycache__" or "node_modules" in filepath.parts:
            continue
        if filepath.suffix.lower() not in extensions:
            continue
        # 集約結果を自分自身に再集約しないよう除外
        if filepath.resolve() == resolved_output:
            continue
        yield filepath


def aggregate_texts(
    target_dir: str,
    output_file: str,
    max_size_str: str,
    extensions: frozenset[str] | None = None,
) -> None:
    """
    ディレクトリ内テキストの集約と分割出力

    HTML は Markdown 変換、その他はそのまま読み込み、gitingest 形式へ集約

    Args:
        target_dir (str): 集約対象ディレクトリ
        output_file (str): 出力ファイルパス
        max_size_str (str): 1ファイル上限の表記（例: 500KB）
        extensions (frozenset[str] | None): 対象拡張子集合（未指定時は既定値）
    """
    target_path = Path(target_dir)
    if not target_path.is_dir():
        logger.error("指定されたディレクトリ '%s' が見つかりません", target_dir)
        return

    size_limit = parse_size(max_size_str)
    target_extensions = extensions if extensions is not None else DEFAULT_EXTENSIONS

    # 収集データを格納 { "仮想ファイルパス": "中身のテキスト" }
    ingested_data: dict[str, str] = {}

    logger.info("[%s] 内のファイルをスキャン中...", target_path.resolve())

    for filepath in _iter_target_files(
        target_path, target_extensions, Path(output_file)
    ):
        # ターゲットディレクトリからの相対パス（スラッシュ区切り）
        rel_path = filepath.relative_to(target_path).as_posix()
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            logger.warning("ファイルの読み込みに失敗しました (%s): %s", rel_path, error)
            continue

        if filepath.suffix.lower() in (".html", ".htm"):
            # HTML は Markdown へ変換し、拡張子を .md にした仮想パスで保持
            markdown_content = _html_to_markdown(content)
            virtual_path = str(Path(rel_path).with_suffix(".md"))
            ingested_data[virtual_path] = markdown_content
            logger.info("[Parsed HTML -> MD] %s -> %s", rel_path, virtual_path)
        else:
            ingested_data[rel_path] = content
            logger.info("[Read Text] %s", rel_path)

    if not ingested_data:
        logger.warning("集約対象のテキストファイルが見つかりませんでした")
        return

    sections = build_sections(ingested_data)
    write_bundle(sections, output_file, size_limit)


def cli() -> None:
    """
    コマンドライン入力の解釈と集約処理の起動
    """
    parser = argparse.ArgumentParser(
        description=(
            "指定したディレクトリ内のテキストファイルおよび"
            "HTMLファイル（Markdown変換）を集約・分割出力します。"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "target_dir",
        type=str,
        help="集約したい対象のディレクトリパス (例: ./src)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default="aggregated_code.txt",
        help="出力ファイル名 (デフォルト: aggregated_code.txt)",
    )
    parser.add_argument(
        "-m",
        "--max-size",
        type=str,
        default="1MB",
        help=(
            "1ファイルあたりの最大サイズ (例: 500KB, 1MB, 1048576)\n"
            "50000w のような単語数指定も可能。デフォルトは 1MB"
        ),
    )

    args = parser.parse_args()
    aggregate_texts(args.target_dir, args.output, args.max_size)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cli()
