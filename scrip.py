import argparse
import re
import os
from urllib.parse import urljoin, urlparse
from pathlib import Path, PurePosixPath
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify
import mdformat


def parse_size(size_str: str) -> int:
    """人間が読みやすいサイズ表記（500KB, 1MB等）をバイト数（int）に変換する"""
    size_str = size_str.strip().upper()
    try:
        if size_str.endswith('KB'):
            return int(float(size_str[:-2].strip()) * 1024)
        elif size_str.endswith('MB'):
            return int(float(size_str[:-2].strip()) * 1024 * 1024)
        elif size_str.endswith('GB'):
            return int(float(size_str[:-2].strip()) * 1024 * 1024 * 1024)
        return int(size_str)
    except ValueError as e:
        raise ValueError(
            f"無効なサイズ指定です: '{size_str}'。数値または単位付きの文字列（例: 500KB, 1.5MB）を指定してください。"
        ) from e


# リンクとして辿る（＝成果物に含まれうる）テキスト系の拡張子
ALLOWED_LINK_EXTENSIONS = {
    '', '.html', '.htm', '.txt', '.md', '.rst', '.xml', '.json',
    '.php', '.jsp', '.asp', '.aspx', '.shtml', '.cgi'
}


def url_to_bundle_path(url: str, flat: bool = False) -> str:
    """クロール対象URLを成果物内の相対ファイルパスに変換する。
    flat=False: 'folder/file.md'（結合モードの File: 見出しと一致）
    flat=True : 'folder_file.md'（--no-merge の個別ファイル名と一致）"""
    relative_path = urlparse(url).path.strip('/')
    path_obj = PurePosixPath(relative_path)
    if path_obj.suffix.lower() in {'.html', '.htm', '.txt', '.md'}:
        base_path = str(path_obj.with_suffix(''))
    else:
        base_path = relative_path
    name = 'index' if (not base_path or base_path == 'docs') else base_path
    return (name.replace('/', '_') if flat else name) + '.md'


def rewrite_links_for_bundle(html: str, current_url: str,
                             limit_prefix_norm: str, flat: bool) -> str:
    """本文HTML中の内部リンク（クロール対象内のテキストページ）を、
    成果物内のファイルパスへ書き換える。外部・非テキスト・対象外のリンクは変更しない。"""
    frag = BeautifulSoup(html, 'html.parser')
    for a in frag.find_all('a', href=True):
        href = a['href']
        if href.lstrip().startswith('#'):
            continue  # 同一ページ内アンカーはそのまま残す
        clean_url = urljoin(current_url, href).split('#')[0].rstrip('/')
        suffix = Path(urlparse(clean_url).path).suffix.lower()
        is_numeric = bool(re.match(r'^\.[0-9]+$', suffix))
        is_text = suffix in ALLOWED_LINK_EXTENSIONS or is_numeric
        if is_text and clean_url.startswith(limit_prefix_norm):
            a['href'] = url_to_bundle_path(clean_url, flat=flat)
        # それ以外は変更しない
    return str(frag)


# --- メイン処理関数 ---
def main(args):
    # --- 設定 ---
    START_URL = args.url
    LIMIT_PREFIX = args.prefix if args.prefix else START_URL
    OUTPUT_FILE = args.output
    MAX_SIZE = parse_size(args.max_size)

    # クロール対象を制限するための前方一致接頭辞
    limit_prefix_norm = LIMIT_PREFIX.rstrip('/')

    visited = set()
    to_visit = [START_URL]
    retry_counts = {}

    # クロールしたデータを保持する辞書 { "ファイルパス": "Markdown本文" }
    ingested_data = {}

    print(f"クロールを開始します (開始URL: {START_URL}, 制限接頭辞: {limit_prefix_norm})")

    # ブラウザへのなりすまし用セッションの作成とヘッダーの設定
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en;q=0.9,en-GB;q=0.8,en-US;q=0.7",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "sec-ch-ua-platform": "macOS",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "\"none\"",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0"
    })

    if args.html or args.no_merge:
        out_dir = Path(OUTPUT_FILE)
        out_dir.mkdir(parents=True, exist_ok=True)

    while to_visit:
        current_url = to_visit.pop(0)
        
        # 重複排除のため末尾スラッシュを正規化
        normalized_url = current_url.rstrip('/')
        if normalized_url in visited:
            continue
        
        print(f"Processing: {current_url}")

        try:
            # 1. ページを取得 (セッション経由でブラウザヘッダーを使用)
            res = session.get(current_url, timeout=10)
            res.raise_for_status()
            
            # レスポンスの文字コードを設定（ヘッダー指定がない場合のデフォルト値 ISO-8859-1 を補正）
            if res.encoding == 'ISO-8859-1' or not res.encoding:
                res.encoding = res.apparent_encoding or 'utf-8'
                
            soup = BeautifulSoup(res.content, 'html.parser', from_encoding=res.encoding)

            parsed_url = urlparse(current_url)
            relative_path = parsed_url.path.strip('/')

            if args.html:
                content_to_save = res.text.lstrip('\ufeff')
                # 階層は無視し、スラッシュをアンダースコアに置換してフラットなファイル名にする
                if not relative_path or relative_path == 'docs':
                    file_name = "index.html"
                else:
                    file_name = relative_path.replace('/', '_') + ".html"
                
                # 随時保存（ファイルへ即座に書き出し）
                file_path = out_dir / file_name
                with open(file_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(content_to_save)
                print(f"  [Saved] {file_path}")
            else:
                # 2. markdownifyでMarkdown変換を実行
                # ナビゲーション等のノイズを避けるため、可能なら main または article 要素を抽出する
                main_content = soup.find('main') or soup.find('article')
                src_html = str(main_content) if main_content else res.text

                # 内部リンクを成果物内のパスへ書き換える（再パースするため soup は汚さない）
                html_to_convert = rewrite_links_for_bundle(
                    src_html, current_url, limit_prefix_norm, flat=args.no_merge
                )

                raw_markdown = markdownify(
                    html_to_convert,
                    heading_style="ATX",
                    strip=['img']
                )

                # 万が一何も抽出されなかった場合のフォールバック
                if not raw_markdown or not raw_markdown.strip():
                    raw_markdown = soup.get_text(separator="\n", strip=True)

                # 4. mdformatでフォーマット
                try:
                    content_to_save = mdformat.text(raw_markdown)
                except Exception as fmt_e:
                    content_to_save = raw_markdown
                
                content_to_save = content_to_save.lstrip('\ufeff')

                if args.no_merge:
                    file_name = url_to_bundle_path(current_url, flat=True)
                    file_path = out_dir / file_name
                    with open(file_path, "w", encoding="utf-8", newline="\n") as f:
                        f.write(content_to_save)
                    print(f"  [Saved Markdown] {file_path}")
                else:
                    file_path = url_to_bundle_path(current_url, flat=False)
                    ingested_data[file_path] = content_to_save

            # 正常に処理が完了したらvisitedに追加
            visited.add(normalized_url)

            # 6. ページ内のリンクを抽出してキューに追加
            for link in soup.find_all('a', href=True):
                full_url = urljoin(current_url, link['href'])
                clean_url = full_url.split('#')[0].rstrip('/')
                
                # 拡張子によるテキスト以外の除外判定
                parsed_clean = urlparse(clean_url)
                suffix = Path(parsed_clean.path).suffix.lower()
                
                # ドット＋数値のパターン（例: .1, .123）も暗黙的にHTML（テキスト）として許可する
                is_numeric_suffix = bool(re.match(r'^\.[0-9]+$', suffix))
                
                allowed_extensions = {
                    '', '.html', '.htm', '.txt', '.md', '.rst', '.xml', '.json',
                    '.php', '.jsp', '.asp', '.aspx', '.shtml', '.cgi'
                }
                
                if suffix not in allowed_extensions and not is_numeric_suffix:
                    print(f"  [Skipped Non-Text] {clean_url} (拡張子: {suffix})")
                    continue
                
                if clean_url.startswith(limit_prefix_norm) and clean_url not in visited and clean_url not in to_visit:
                    to_visit.append(clean_url)

        except requests.exceptions.RequestException as e:
            # 404エラーの場合はリトライせずにスキップ
            if isinstance(e, requests.exceptions.HTTPError) and e.response is not None and e.response.status_code == 404:
                print(f"  [Error] 404 Not Found ({current_url}): リトライせずにスキップします")
                visited.add(normalized_url)
            else:
                retry_counts[normalized_url] = retry_counts.get(normalized_url, 0) + 1
                current_retry = retry_counts[normalized_url]
                if current_retry <= 3:
                    print(f"  [Error] ネットワークエラー ({current_url}): {e} (リトライ {current_retry}/3: キューの最後に追加します)")
                    to_visit.append(current_url)
                else:
                    print(f"  [Error] ネットワークエラー ({current_url}): {e} (リトライ上限に達したためスキップします)")
                    visited.add(normalized_url)
        except Exception as e:
            retry_counts[normalized_url] = retry_counts.get(normalized_url, 0) + 1
            current_retry = retry_counts[normalized_url]
            if current_retry <= 3:
                print(f"  [Error] 処理失敗 ({current_url}): {e} (リトライ {current_retry}/3: キューの最後に追加します)")
                to_visit.append(current_url)
            else:
                print(f"  [Error] 処理失敗 ({current_url}): {e} (リトライ上限に達したためスキップします)")
                visited.add(normalized_url)

    if args.html:
        print(f"\n完了！ すべてのHTMLファイルを個別保存（随時保存）しました。 (合計: {len(visited)} ページ)")
        return

    if args.no_merge:
        print(f"\n完了！ すべてのMarkdownファイルを個別保存（随時保存）しました。 (合計: {len(visited)} ページ)")
        return

    # ==========================================
    # Gitingest風の1ファイルへの結合・出力処理
    # ==========================================
    print("\nクロール完了。結合ファイルを作成しています...")

    # ツリー構造を生成するヘルパー関数
    def generate_tree_str(paths):
        tree = {}
        for path in paths:
            parts = path.split('/')
            current = tree
            for part in parts:
                if part not in current:
                    current[part] = {}
                current = current[part]
        
        def format_tree(node, prefix=""):
            lines = []
            pointers = ["├── "] * (len(node) - 1) + ["└── "] if node else []
            for pointer, (key, child) in zip(pointers, sorted(node.items())):
                lines.append(prefix + pointer + key)
                if child:
                    extension = "│   " if pointer == "├── " else "    "
                    lines.extend(format_tree(child, prefix + extension))
            return lines
        
        return "\n".join(["."] + format_tree(tree))

    # セクションリストの作成
    sections = []
    
    # 1. ディレクトリツリーを最初のセクションとする
    tree_header = "Directory structure:\n" + generate_tree_str(ingested_data.keys()) + "\n\n"
    sections.append(tree_header)
    
    # 2. 各ファイルの内容を出力（セパレーター付き）
    for path, content in sorted(ingested_data.items()):
        clean_content = content.replace('\r\n', '\n')
        section_str = (
            "=" * 48 + "\n" +
            f"File: {path}\n" +
            "=" * 48 + "\n" +
            clean_content + "\n\n"
        )
        sections.append(section_str)

    # 3. サイズ制限付き分割保存
    out_path = Path(OUTPUT_FILE)
    out_dir = out_path.parent
    if out_dir != Path('.'):
        out_dir.mkdir(parents=True, exist_ok=True)
        
    stem = out_path.stem
    suffix = out_path.suffix

    # 全コンテンツの総サイズを計算
    total_bytes = sum(len(sec.encode("utf-8")) for sec in sections)

    if total_bytes <= MAX_SIZE:
        # 分割不要な場合は、指定された元のファイル名でそのまま出力
        with open(OUTPUT_FILE, "w", encoding="utf-8", newline="\n") as f:
            first = True
            for sec in sections:
                if first:
                    f.write(sec.lstrip('\ufeff'))
                    first = False
                else:
                    f.write(sec)
        print(f"\n完了！ '{OUTPUT_FILE}' に出力しました。 (総サイズ: {total_bytes} バイト)")
    else:
        # 分割が必要な場合
        file_idx = 1
        current_content = ""
        
        for section in sections:
            section_bytes_len = len(section.encode("utf-8"))
            next_filename = f"{stem}_part_{file_idx + 1}{suffix}"
            footer = f"\n\n>>> NOTE: This file has been split. Continued in: {next_filename}\n"
            
            current_len = len(current_content.encode("utf-8"))
            footer_len = len(footer.encode("utf-8"))
            
            if current_len > 0 and (current_len + section_bytes_len + footer_len) > MAX_SIZE:
                part_filename = out_dir / f"{stem}_part_{file_idx}{suffix}"
                with open(part_filename, "w", encoding="utf-8", newline="\n") as f:
                    f.write((current_content + footer).lstrip('\ufeff'))
                print(f"Created: {part_filename} (サイズ: {current_len + footer_len} バイト)")
                
                current_content = section
                file_idx += 1
            else:
                current_content += section
                
        # 残余コンテンツの書き込み
        if current_content:
            part_filename = out_dir / f"{stem}_part_{file_idx}{suffix}"
            with open(part_filename, "w", encoding="utf-8", newline="\n") as f:
                f.write(current_content.lstrip('\ufeff'))
            print(f"Created: {part_filename} (サイズ: {len(current_content.encode('utf-8'))} バイト)")
            
        print(f"\n完了！ ファイルサイズ超過のため {file_idx} 個に分割して出力しました。 (制限上限: {args.max_size})")


if __name__ == "__main__":
    # --- 引数パース ---
    parser = argparse.ArgumentParser(
        description="ドキュメントサイトを読み込み、1つのテキストファイルにまとめるスクリプト (gitingest形式出力)",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "-u", "--url",
        type=str,
        required=True,
        help="クロールを開始するURL (エントリーポイント)"
    )
    parser.add_argument(
        "-p", "--prefix",
        type=str,
        help="クロール対象を制限するためのURL前方一致接頭辞 (指定しない場合は --url と同等になります)"
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        required=True,
        help="出力ファイル名"
    )
    parser.add_argument(
        "-m", "--max-size",
        type=str,
        default="1MB",
        help="1ファイルあたりの最大サイズ (例: 500KB, 1MB, 1048576)。デフォルトは 1MB"
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="Markdown変換を行わずに、生のHTMLとして保存する"
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="結合せず、各ページを個別のMarkdownファイルとして保存する。この時、-o/--output は出力先ディレクトリ名になります"
    )
    args = parser.parse_args()

    # 処理実行
    main(args)