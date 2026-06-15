import os
import argparse
from pathlib import Path
from bs4 import BeautifulSoup
import trafilatura
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
        elif size_str.endswith('B'):
            return int(float(size_str[:-1].strip()))
        return int(size_str)
    except ValueError as e:
        raise ValueError(
            f"無効なサイズ指定です: '{size_str}'。数値または単位付きの文字列（例: 200B, 500KB, 1.5MB）を指定してください。"
        ) from e


def generate_tree_str(paths):
    """ファイルパスのリストから、Gitingest形式のディレクトリツリー文字列を生成する"""
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


def aggregate_texts(target_dir, output_file, max_size_str, extensions=None):
    """対象ディレクトリ内のテキストファイルを収集し、HTMLはMarkdownに変換して単一または分割ファイルに集約する"""
    target_path = Path(target_dir)
    if not target_path.exists() or not target_path.is_dir():
        print(f"エラー: 指定されたディレクトリ '{target_dir}' が見つかりません。")
        return

    MAX_SIZE = parse_size(max_size_str)
    
    # デフォルトのテキスト系拡張子
    if extensions is None:
        extensions = {'.txt', '.py', '.md', '.csv', '.json', '.html', '.htm', '.js', '.ts', '.css', '.sh', '.yaml', '.yml'}

    # 収集したデータを格納 { "仮想ファイルパス": "中身のテキスト" }
    ingested_data = {}

    print(f"[{target_path.resolve()}] 内のファイルをスキャン中...")

    # rglobで全ファイルを走査
    for filepath in sorted(target_path.rglob('*')):
        if filepath.is_dir():
            continue
        if any(part.startswith('.') for part in filepath.parts):
            continue
        if filepath.name == '__pycache__' or 'node_modules' in filepath.parts:
            continue
        if filepath.suffix.lower() not in extensions:
            continue

        # 集約されたファイルを自分自身に再度集約しないように除外
        if output_file and filepath.resolve() == Path(output_file).resolve():
            continue

        # ターゲットディレクトリからの相対パスを取得し、スラッシュ区切りにする
        rel_path = filepath.relative_to(target_path).as_posix()

        try:
            # ファイルの読み込み
            if filepath.suffix.lower() in ('.html', '.htm'):
                # HTMLファイルの場合：Markdownへのパースと変換を行う
                with open(filepath, 'r', encoding='utf-8', errors='replace') as infile:
                    html_content = infile.read()

                # trafilaturaで高精度な本文抽出とMarkdown変換を実行
                raw_markdown = trafilatura.extract(
                    html_content,
                    output_format="markdown",
                    include_links=True,
                    include_images=False
                )

                # 何も抽出されなかった場合のフォールバック（BeautifulSoup）
                if not raw_markdown:
                    soup = BeautifulSoup(html_content, 'html.parser')
                    raw_markdown = soup.get_text(separator="\n", strip=True)

                # mdformatでフォーマット
                try:
                    markdown_content = mdformat.text(raw_markdown)
                except Exception:
                    markdown_content = raw_markdown

                # 拡張子を .md に変更して仮想ファイルパスにする
                virtual_path = str(Path(rel_path).with_suffix('.md'))
                ingested_data[virtual_path] = markdown_content
                print(f"  [Parsed HTML -> MD] {rel_path} -> {virtual_path}")
            else:
                # 一般テキストファイルの場合：そのまま読み込み
                with open(filepath, 'r', encoding='utf-8', errors='replace') as infile:
                    content = infile.read()
                ingested_data[rel_path] = content
                print(f"  [Read Text] {rel_path}")

        except Exception as e:
            print(f"  [Warning] ファイルの読み込みに失敗しました ({rel_path}): {e}")

    if not ingested_data:
        print("集約対象のテキストファイルが見つかりませんでした。")
        return

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
    out_path = Path(output_file)
    out_dir = out_path.parent
    if out_dir != Path('.'):
        out_dir.mkdir(parents=True, exist_ok=True)
        
    stem = out_path.stem
    suffix = out_path.suffix

    # 全コンテンツの総サイズを計算
    total_bytes = sum(len(sec.encode("utf-8")) for sec in sections)

    if total_bytes <= MAX_SIZE:
        # 分割不要な場合はそのまま出力
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            for sec in sections:
                f.write(sec)
        print(f"\n完了！ '{out_path}' に出力しました。 (総サイズ: {total_bytes} バイト)")
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
                    f.write(current_content + footer)
                print(f"Created: {part_filename} (サイズ: {current_len + footer_len} バイト)")
                
                current_content = section
                file_idx += 1
            else:
                current_content += section
                
        # 残余コンテンツの書き込み
        if current_content:
            part_filename = out_dir / f"{stem}_part_{file_idx}{suffix}"
            with open(part_filename, "w", encoding="utf-8", newline="\n") as f:
                f.write(current_content)
            print(f"Created: {part_filename} (サイズ: {len(current_content.encode('utf-8'))} バイト)")
            
        print(f"\n完了！ ファイルサイズ超過のため {file_idx} 個に分割して出力しました。 (制限上限: {max_size_str})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="指定したディレクトリ内のテキストファイルおよびHTMLファイル（Markdown変換）を集約・分割出力します。",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "target_dir",
        type=str,
        help="集約したい対象のディレクトリパス (例: ./src)"
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default="aggregated_code.txt",
        help="出力ファイル名 (デフォルト: aggregated_code.txt)"
    )
    parser.add_argument(
        "-m", "--max-size",
        type=str,
        default="1MB",
        help="1ファイルあたりの最大サイズ (例: 500KB, 1MB, 1048576)。デフォルトは 1MB"
    )
    
    args = parser.parse_args()
    
    aggregate_texts(args.target_dir, args.output, args.max_size)