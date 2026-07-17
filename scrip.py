"""
Web ドキュメントクローラ

起点 URL から同一接頭辞配下を再帰的に辿り、HTML を Markdown 変換して
gitingest 形式の単一（または分割/個別）ファイルへ集約する CLI
"""

from __future__ import annotations

import argparse
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse

import mdformat
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify

from common import build_sections, is_text_link_suffix, parse_size, write_bundle

logger = logging.getLogger("web2md")

# リトライ上限回数
MAX_RETRIES = 3

# ブラウザなりすまし用の HTTP ヘッダー
_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "en;q=0.9,en-GB;q=0.8,en-US;q=0.7",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua-platform": "macOS",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": '"none"',
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


@dataclass(frozen=True)
class CrawlConfig:
    """
    クロール実行設定

    Attributes:
        output (str): 出力ファイル/ディレクトリ
        max_size (int): 1ファイル上限バイト数
        max_size_str (str): 上限の元表記
        as_html (bool): 生 HTML 保存モード
        no_merge (bool): Markdown 個別保存モード
        delay_min (float): リクエスト間待機のランダム下限秒数
        delay_max (float): リクエスト間待機のランダム上限秒数
        start_url (str | None): クロール開始 URL
        limit_prefix (str | None): クロール許可の接頭辞
        local_path (str | None): ローカルのファイルまたはディレクトリパス
    """

    output: str
    max_size: int
    max_size_str: str
    as_html: bool
    no_merge: bool
    delay_min: float
    delay_max: float
    start_url: str | None = None
    limit_prefix: str | None = None
    local_path: str | None = None

    def next_delay(self) -> float:
        """
        次リクエストまでのランダム待機秒数算出

        下限以上・上限以下の一様乱数、上限が下限以下なら下限を返す

        Returns:
            float: 待機秒数
        """
        if self.delay_max <= self.delay_min:
            return self.delay_min
        return random.uniform(self.delay_min, self.delay_max)


def relative_path_to_bundle_path(rel_path_str: str, flat: bool = False) -> str:
    """
    相対パスから成果物内相対パスへの変換

    Args:
        rel_path_str (str): スラッシュ区切りの相対パス
        flat (bool): フラットなファイル名へ変換するか

    Returns:
        str: 成果物内の相対パス
    """
    path_obj = PurePosixPath(rel_path_str)
    if path_obj.suffix.lower() in {".html", ".htm", ".txt", ".md"}:
        base_path = str(path_obj.with_suffix(""))
    else:
        base_path = rel_path_str
    name = "index" if (not base_path or base_path == "docs") else base_path
    return (name.replace("/", "_") if flat else name) + ".md"


def url_to_bundle_path(url: str, flat: bool = False) -> str:
    """
    クロール対象 URL の成果物内相対パスへの変換

    flat=False は `folder/file.md`（結合時の File 見出し）
    flat=True は `folder_file.md`（--no-merge の個別ファイル名）に対応

    Args:
        url (str): 対象 URL
        flat (bool): フラットなファイル名へ変換するか

    Returns:
        str: 成果物内の相対パス
    """
    relative_path = urlparse(url).path.strip("/")
    return relative_path_to_bundle_path(relative_path, flat=flat)


def rewrite_links_for_bundle(
    html: str, current_url: str, limit_prefix_norm: str, flat: bool
) -> str:
    """
    本文 HTML 内の内部リンクの成果物パス書き換え

    クロール対象内のテキストページへのリンクを成果物内パスへ変換し、
    外部・非テキスト・同一ページ内アンカーは変更しない

    Args:
        html (str): 本文 HTML
        current_url (str): 現在ページの URL
        limit_prefix_norm (str): クロール許可接頭辞
        flat (bool): フラットなファイル名へ変換するか

    Returns:
        str: リンク書き換え後の HTML
    """
    frag = BeautifulSoup(html, "html.parser")
    for anchor in frag.find_all("a", href=True):
        href = str(anchor["href"])
        if href.lstrip().startswith("#"):
            continue  # 同一ページ内アンカーはそのまま残す
        clean_url = urljoin(current_url, href).split("#")[0].rstrip("/")
        suffix = Path(urlparse(clean_url).path).suffix.lower()
        if is_text_link_suffix(suffix) and clean_url.startswith(limit_prefix_norm):
            anchor["href"] = url_to_bundle_path(clean_url, flat=flat)
        # それ以外は変更しない
    return str(frag)


def build_session() -> requests.Session:
    """
    ブラウザ相当ヘッダー付き HTTP セッション生成

    Returns:
        requests.Session: 設定済みセッション
    """
    session = requests.Session()
    session.headers.update(_BROWSER_HEADERS)
    return session


def _resolve_encoding(response: requests.Response) -> str:
    """
    レスポンス文字コードの決定

    ヘッダー未指定の既定値 ISO-8859-1 を推定エンコーディングで補正

    Args:
        response (requests.Response): HTTP レスポンス

    Returns:
        str: 採用する文字コード
    """
    if response.encoding == "ISO-8859-1" or not response.encoding:
        return response.apparent_encoding or "utf-8"
    return response.encoding


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
        logger.warning("mdformat による整形に失敗しました: %s", error)
        return raw_markdown


def convert_to_markdown(
    soup: BeautifulSoup,
    raw_html: str,
    current_url: str,
    config: CrawlConfig,
) -> str:
    """
    ページ HTML の Markdown 変換

    main/article 要素を優先抽出し、内部リンク書き換え後に Markdown 化、
    抽出失敗時はテキスト抽出へフォールバック

    Args:
        soup (BeautifulSoup): パース済み DOM
        raw_html (str): ページ生 HTML
        current_url (str): 現在ページの URL
        config (CrawlConfig): クロール設定

    Returns:
        str: 整形済み Markdown
    """
    # ナビゲーション等のノイズを避けるため可能なら main / article を抽出
    main_content = soup.find("main") or soup.find("article")
    src_html = str(main_content) if main_content else raw_html

    assert config.limit_prefix is not None
    html_to_convert = rewrite_links_for_bundle(
        src_html, current_url, config.limit_prefix, flat=config.no_merge
    )

    raw_markdown = markdownify(html_to_convert, heading_style="ATX", strip=["img"])

    # 何も抽出されなかった場合のフォールバック
    if not raw_markdown or not raw_markdown.strip():
        raw_markdown = soup.get_text(separator="\n", strip=True)

    return _format_markdown(raw_markdown).lstrip("\ufeff")


def resolve_local_link(href: str, current_file: Path, base_dir: Path) -> Path | None:
    """
    ローカル HTML 内の href リンクをローカル絶対パスへ解決

    ベースディレクトリ配下で存在するもののみ返す

    Args:
        href (str): リンク先文字列
        current_file (Path): 処理中ファイルのパス
        base_dir (Path): 探索起点となるベースディレクトリ

    Returns:
        Path | None: 解決されたローカル絶対パス
    """
    # 外部URLやスキーム付き、アンカーのみは除外
    if (
        href.startswith("#")
        or href.startswith("javascript:")
        or ":" in href
        or href.startswith("//")
    ):
        return None

    if href.startswith("/"):
        resolved = base_dir / href.lstrip("/")
    else:
        resolved = (current_file.parent / href).resolve()

    try:
        resolved = resolved.resolve()
        if resolved.is_relative_to(base_dir.resolve()) and resolved.exists():
            return resolved
    except (ValueError, RuntimeError):
        pass
    return None


def rewrite_local_links(
    html: str, current_file: Path, base_dir: Path, flat: bool
) -> str:
    """
    ローカル HTML 内のリンクを成果物パスへ書き換え

    Args:
        html (str): 本文 HTML
        current_file (Path): 処理中ファイルのパス
        base_dir (Path): 探索起点となるベースディレクトリ
        flat (bool): フラットなファイル名へ変換するか

    Returns:
        str: リンク書き換え後の HTML
    """
    frag = BeautifulSoup(html, "html.parser")
    for anchor in frag.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if href.startswith("#"):
            continue
        resolved_path = resolve_local_link(href, current_file, base_dir)
        if resolved_path and resolved_path.suffix.lower() in {".html", ".htm"}:
            rel_path_str = str(resolved_path.relative_to(base_dir))
            anchor["href"] = relative_path_to_bundle_path(rel_path_str, flat=flat)
    return str(frag)


def convert_local_to_markdown(
    soup: BeautifulSoup,
    raw_html: str,
    current_file: Path,
    base_dir: Path,
    config: CrawlConfig,
) -> str:
    """
    ローカル HTML の Markdown 変換

    Args:
        soup (BeautifulSoup): パース済み DOM
        raw_html (str): ページ生 HTML
        current_file (Path): 処理中ファイルのパス
        base_dir (Path): 探索起点となるベースディレクトリ
        config (CrawlConfig): クロール設定

    Returns:
        str: 整形済み Markdown
    """
    main_content = soup.find("main") or soup.find("article")
    src_html = str(main_content) if main_content else raw_html

    html_to_convert = rewrite_local_links(
        src_html, current_file, base_dir, flat=config.no_merge
    )

    raw_markdown = markdownify(html_to_convert, heading_style="ATX", strip=["img"])

    if not raw_markdown or not raw_markdown.strip():
        raw_markdown = soup.get_text(separator="\n", strip=True)

    return _format_markdown(raw_markdown).lstrip("\ufeff")


def process_local_directory(config: CrawlConfig) -> None:
    """
    ローカルディレクトリ内の HTML ファイルを再帰探索して処理

    Args:
        config (CrawlConfig): クロール設定
    """
    assert config.local_path is not None
    base_dir = Path(config.local_path).resolve()
    if not base_dir.is_dir():
        logger.error("指定されたパスはディレクトリではありません: %s", base_dir)
        return

    html_files: list[Path] = []
    for ext in ("*.html", "*.htm"):
        html_files.extend(base_dir.rglob(ext))

    html_files.sort()

    ingested_data: dict[str, str] = {}
    out_dir = Path(config.output)
    if config.as_html or config.no_merge:
        out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("ローカルディレクトリ再帰探索を開始します (ディレクトリ: %s)", base_dir)

    for file_path in html_files:
        logger.info("Processing: %s", file_path)
        try:
            raw_html = file_path.read_text(encoding="utf-8", errors="replace")
            soup = BeautifulSoup(raw_html, "html.parser")

            rel_path_str = str(file_path.relative_to(base_dir))
            bundle_path = relative_path_to_bundle_path(
                rel_path_str, flat=config.no_merge
            )

            if config.as_html:
                flat_name = rel_path_str.replace("/", "_")
                dest_path = out_dir / (Path(flat_name).with_suffix(".html"))
                dest_path.write_text(raw_html, encoding="utf-8", newline="\n")
                logger.info("[Saved] %s", dest_path)
            elif config.no_merge:
                markdown = convert_local_to_markdown(
                    soup, raw_html, file_path, base_dir, config
                )
                dest_path = out_dir / bundle_path
                dest_path.write_text(markdown, encoding="utf-8", newline="\n")
                logger.info("[Saved Markdown] %s", dest_path)
            else:
                markdown = convert_local_to_markdown(
                    soup, raw_html, file_path, base_dir, config
                )
                bundle_path_no_flat = relative_path_to_bundle_path(
                    rel_path_str, flat=False
                )
                ingested_data[bundle_path_no_flat] = markdown

        except Exception as error:
            logger.error(
                "ファイルの処理中にエラーが発生しました (%s): %s", file_path, error
            )

    if not (config.as_html or config.no_merge):
        _finalize(config, ingested_data, len(html_files))


def process_local_file_links(config: CrawlConfig) -> None:
    """
    起点となる HTML 内のリンクからローカル HTML を再帰追跡して処理

    Args:
        config (CrawlConfig): クロール設定
    """
    assert config.local_path is not None
    start_file = Path(config.local_path).resolve()
    if not start_file.is_file():
        logger.error("指定されたパスはファイルではありません: %s", start_file)
        return

    base_dir = start_file.parent

    visited: set[Path] = set()
    to_visit: list[Path] = [start_file]
    ingested_data: dict[str, str] = {}
    out_dir = Path(config.output)
    if config.as_html or config.no_merge:
        out_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "ローカルファイルリンク再帰追跡を開始します (起点ファイル: %s)", start_file
    )

    while to_visit:
        current_file = to_visit.pop(0)
        current_file = current_file.resolve()
        if current_file in visited:
            continue

        logger.info("Processing: %s", current_file)

        try:
            raw_html = current_file.read_text(encoding="utf-8", errors="replace")
            soup = BeautifulSoup(raw_html, "html.parser")

            rel_path_str = str(current_file.relative_to(base_dir))
            bundle_path = relative_path_to_bundle_path(
                rel_path_str, flat=config.no_merge
            )

            if config.as_html:
                flat_name = rel_path_str.replace("/", "_")
                dest_path = out_dir / (Path(flat_name).with_suffix(".html"))
                dest_path.write_text(raw_html, encoding="utf-8", newline="\n")
                logger.info("[Saved] %s", dest_path)
            elif config.no_merge:
                markdown = convert_local_to_markdown(
                    soup, raw_html, current_file, base_dir, config
                )
                dest_path = out_dir / bundle_path
                dest_path.write_text(markdown, encoding="utf-8", newline="\n")
                logger.info("[Saved Markdown] %s", dest_path)
            else:
                markdown = convert_local_to_markdown(
                    soup, raw_html, current_file, base_dir, config
                )
                bundle_path_no_flat = relative_path_to_bundle_path(
                    rel_path_str, flat=False
                )
                ingested_data[bundle_path_no_flat] = markdown

            visited.add(current_file)

            for anchor in soup.find_all("a", href=True):
                href = str(anchor["href"]).strip()
                resolved = resolve_local_link(href, current_file, base_dir)
                if resolved and resolved.suffix.lower() in {".html", ".htm"}:
                    resolved_resolved = resolved.resolve()
                    if (
                        resolved_resolved not in visited
                        and resolved_resolved not in to_visit
                    ):
                        to_visit.append(resolved_resolved)

        except Exception as error:
            logger.error(
                "ファイルの処理中にエラーが発生しました (%s): %s", current_file, error
            )

    if not (config.as_html or config.no_merge):
        _finalize(config, ingested_data, len(visited))


def enqueue_links(
    soup: BeautifulSoup,
    current_url: str,
    config: CrawlConfig,
    visited: set[str],
    to_visit: list[str],
) -> None:
    """
    ページ内リンクのクロールキュー追加

    テキスト系拡張子かつ接頭辞一致、未訪問・未キューの URL のみ追加

    Args:
        soup (BeautifulSoup): パース済み DOM
        current_url (str): 現在ページの URL
        config (CrawlConfig): クロール設定
        visited (set[str]): 訪問済み URL 集合
        to_visit (list[str]): クロール待ちキュー
    """
    assert config.limit_prefix is not None
    for link in soup.find_all("a", href=True):
        full_url = urljoin(current_url, str(link["href"]))
        clean_url = full_url.split("#")[0].rstrip("/")
        suffix = Path(urlparse(clean_url).path).suffix.lower()

        if not is_text_link_suffix(suffix):
            logger.debug("[Skipped Non-Text] %s (拡張子: %s)", clean_url, suffix)
            continue

        if (
            clean_url.startswith(config.limit_prefix)
            and clean_url not in visited
            and clean_url not in to_visit
        ):
            to_visit.append(clean_url)


def _save_html(raw_text: str, current_url: str, out_dir: Path) -> None:
    """
    生 HTML のフラットファイル保存

    Args:
        raw_text (str): ページ生 HTML
        current_url (str): 現在ページの URL
        out_dir (Path): 出力ディレクトリ
    """
    relative_path = urlparse(current_url).path.strip("/")
    if not relative_path or relative_path == "docs":
        file_name = "index.html"
    else:
        # 階層は無視しスラッシュをアンダースコアへ置換したフラット名にする
        file_name = relative_path.replace("/", "_") + ".html"
    file_path = out_dir / file_name
    file_path.write_text(raw_text.lstrip("\ufeff"), encoding="utf-8", newline="\n")
    logger.info("[Saved] %s", file_path)


def crawl(config: CrawlConfig) -> None:
    """
    設定に基づくサイトクロールと成果物出力

    Args:
        config (CrawlConfig): クロール設定
    """
    assert config.start_url is not None
    visited: set[str] = set()
    to_visit: list[str] = [config.start_url]
    retry_counts: dict[str, int] = {}
    ingested_data: dict[str, str] = {}

    logger.info(
        "クロールを開始します (開始URL: %s, 制限接頭辞: %s)",
        config.start_url,
        config.limit_prefix,
    )

    session = build_session()

    out_dir = Path(config.output)
    if config.as_html or config.no_merge:
        out_dir.mkdir(parents=True, exist_ok=True)

    while to_visit:
        current_url = to_visit.pop(0)
        normalized_url = current_url.rstrip("/")
        if normalized_url in visited:
            continue

        logger.info("Processing: %s", current_url)

        try:
            response = session.get(current_url, timeout=10)
            response.raise_for_status()
            response.encoding = _resolve_encoding(response)
            soup = BeautifulSoup(
                response.content, "html.parser", from_encoding=response.encoding
            )

            if config.as_html:
                _save_html(response.text, current_url, out_dir)
            elif config.no_merge:
                markdown = convert_to_markdown(soup, response.text, current_url, config)
                file_path = out_dir / url_to_bundle_path(current_url, flat=True)
                file_path.write_text(markdown, encoding="utf-8", newline="\n")
                logger.info("[Saved Markdown] %s", file_path)
            else:
                markdown = convert_to_markdown(soup, response.text, current_url, config)
                ingested_data[url_to_bundle_path(current_url, flat=False)] = markdown

            visited.add(normalized_url)
            enqueue_links(soup, current_url, config, visited, to_visit)

            # サーバ負荷軽減のためリクエスト間にランダムな待機を挟む
            wait_seconds = config.next_delay()
            if wait_seconds > 0:
                time.sleep(wait_seconds)

        except requests.exceptions.RequestException as error:
            _handle_request_error(
                error, current_url, normalized_url, retry_counts, visited, to_visit
            )

    _finalize(config, ingested_data, len(visited))


def _handle_request_error(
    error: requests.exceptions.RequestException,
    current_url: str,
    normalized_url: str,
    retry_counts: dict[str, int],
    visited: set[str],
    to_visit: list[str],
) -> None:
    """
    リクエスト例外時のリトライ制御

    404 は即時スキップ、その他はリトライ上限までキュー末尾へ再投入

    Args:
        error (requests.exceptions.RequestException): 発生した例外
        current_url (str): 対象 URL
        normalized_url (str): 正規化済み URL
        retry_counts (dict[str, int]): URL 別リトライ回数
        visited (set[str]): 訪問済み URL 集合
        to_visit (list[str]): クロール待ちキュー
    """
    is_not_found = (
        isinstance(error, requests.exceptions.HTTPError)
        and error.response is not None
        and error.response.status_code == 404
    )
    if is_not_found:
        logger.warning("[Error] 404 Not Found (%s): スキップします", current_url)
        visited.add(normalized_url)
        return

    retry_counts[normalized_url] = retry_counts.get(normalized_url, 0) + 1
    current_retry = retry_counts[normalized_url]
    if current_retry <= MAX_RETRIES:
        logger.warning(
            "[Error] ネットワークエラー (%s): %s (リトライ %d/%d)",
            current_url,
            error,
            current_retry,
            MAX_RETRIES,
        )
        to_visit.append(current_url)
    else:
        logger.error(
            "[Error] リトライ上限に達したためスキップします (%s): %s",
            current_url,
            error,
        )
        visited.add(normalized_url)


def _finalize(
    config: CrawlConfig, ingested_data: dict[str, str], page_count: int
) -> None:
    """
    クロール完了後の成果物確定

    個別/HTML 保存モードは完了報告のみ、結合モードは成果物を書き出し

    Args:
        config (CrawlConfig): クロール設定
        ingested_data (dict[str, str]): 収集済みデータ
        page_count (int): 処理ページ数
    """
    if config.as_html:
        logger.info(
            "完了！ HTMLファイルを個別保存しました (合計: %d ページ)", page_count
        )
        return
    if config.no_merge:
        logger.info(
            "完了！ Markdownファイルを個別保存しました (合計: %d ページ)", page_count
        )
        return

    logger.info("クロール完了。結合ファイルを作成しています...")
    sections = build_sections(ingested_data)
    write_bundle(sections, config.output, config.max_size, config.max_size_str)


def cli() -> None:
    """
    コマンドライン入力の解釈とクロール起動
    """
    parser = argparse.ArgumentParser(
        description=(
            "ドキュメントサイトを読み込み、1つのテキストファイルにまとめる"
            "スクリプト (gitingest形式出力)"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-u", "--url", type=str, help="クロールを開始するURL")
    group.add_argument(
        "-l",
        "--local-path",
        type=str,
        help=(
            "ローカルのHTMLファイルまたはディレクトリのパス\n"
            "(ディレクトリなら全HTML探索、ファイルならリンク再帰追跡)"
        ),
    )

    parser.add_argument(
        "-p",
        "--prefix",
        type=str,
        help="クロール対象を制限するURL前方一致接頭辞 (未指定時は --url と同等)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        required=True,
        help="出力ファイル名 (--no-merge/--html 時は出力ディレクトリ名)",
    )
    parser.add_argument(
        "-m",
        "--max-size",
        type=str,
        default="1MB",
        help="1ファイルあたりの最大サイズ (例: 500KB, 1MB, 1048576)。デフォルトは 1MB",
    )
    parser.add_argument(
        "-d",
        "--delay",
        type=float,
        default=0.5,
        help="リクエスト間ランダム待機の下限秒数 (サーバ負荷軽減用)。デフォルトは 0.5",
    )
    parser.add_argument(
        "--delay-max",
        type=float,
        default=1.7,
        help="リクエスト間ランダム待機の上限秒数。デフォルトは 1.7",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="Markdown変換を行わずに生のHTMLとして保存する",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="結合せず各ページを個別のMarkdownファイルとして保存する",
    )
    args = parser.parse_args()

    if args.url:
        start_url = args.url
        limit_prefix = (args.prefix if args.prefix else start_url).rstrip("/")
        config = CrawlConfig(
            start_url=start_url,
            limit_prefix=limit_prefix,
            local_path=None,
            output=args.output,
            max_size=parse_size(args.max_size),
            max_size_str=args.max_size,
            as_html=args.html,
            no_merge=args.no_merge,
            delay_min=args.delay,
            delay_max=args.delay_max,
        )
        crawl(config)
    else:
        config = CrawlConfig(
            start_url=None,
            limit_prefix=None,
            local_path=args.local_path,
            output=args.output,
            max_size=parse_size(args.max_size),
            max_size_str=args.max_size,
            as_html=args.html,
            no_merge=args.no_merge,
            delay_min=args.delay,
            delay_max=args.delay_max,
        )
        local_path_obj = Path(args.local_path)
        if local_path_obj.is_dir():
            process_local_directory(config)
        else:
            process_local_file_links(config)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cli()
