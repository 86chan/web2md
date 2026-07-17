"""ローカルHTML入力モードのテスト"""

from __future__ import annotations

from pathlib import Path

import pytest

from common import SizeLimit
from scrip import (
    CrawlConfig,
    process_local_directory,
    process_local_file_links,
    resolve_local_link,
    rewrite_local_links,
)


def test_resolve_local_link(tmp_path: Path) -> None:
    """
    ローカル HTML 内の href リンクが正しく絶対パスへ解決されることを検証する。

    [事前条件 (Given)]
    ベースディレクトリと、その中にいくつかの HTML ファイルや
    サブディレクトリが存在する状態。
    外部リンク、アンカー、ルート相対パス、相対パスのテスト用 href 文字列を用意。

    [実行 (When)]
    resolve_local_link 関数を実行する。

    [検証 (Then)]
    - 通常の相対パスやルート相対パスは、ベースディレクトリ配下の
      実在するファイルパスへ解決されること。
    - 外部リンク、アンカーのみの href、ベースディレクトリ外のパス、
      実在しないパスは None が返ること。
    """
    base_dir = tmp_path / "site"
    base_dir.mkdir()
    current_file = base_dir / "folder" / "index.html"
    current_file.parent.mkdir()
    current_file.write_text("content", encoding="utf-8")

    target_file = base_dir / "target.html"
    target_file.write_text("content", encoding="utf-8")

    sub_file = base_dir / "folder" / "sub.html"
    sub_file.write_text("content", encoding="utf-8")

    # 1. 正常な相対パス
    res1 = resolve_local_link("sub.html", current_file, base_dir)
    assert res1 == sub_file.resolve()

    res2 = resolve_local_link("../target.html", current_file, base_dir)
    assert res2 == target_file.resolve()

    # 2. ルート相対パス
    res3 = resolve_local_link("/target.html", current_file, base_dir)
    assert res3 == target_file.resolve()

    # 3. 存在しないパス
    assert resolve_local_link("notfound.html", current_file, base_dir) is None

    # 4. 外部リンクやアンカー
    res_ext = resolve_local_link(
        "http://example.com/index.html", current_file, base_dir
    )
    assert res_ext is None
    assert resolve_local_link("#section", current_file, base_dir) is None
    assert resolve_local_link("javascript:void(0)", current_file, base_dir) is None


def test_rewrite_local_links(tmp_path: Path) -> None:
    """
    ローカル HTML 内のリンクが成果物用のパスへ正しく書き換わることを検証する。

    [事前条件 (Given)]
    ベースディレクトリ配下に HTML ファイルが存在し、相互にリンクしている状態。
    HTML 内に正常なローカルリンクと外部リンクを含める。

    [実行 (When)]
    rewrite_local_links 関数を実行する。

    [検証 (Then)]
    - ローカルリンクが成果物内の `.md` パスへ書き換わること
      （フラットフラグに応じたパスになること）。
    - 外部リンクやアンカーは書き換えられずそのまま維持されること。
    """
    base_dir = tmp_path / "site"
    base_dir.mkdir()
    current_file = base_dir / "index.html"
    current_file.write_text("content", encoding="utf-8")

    target_file = base_dir / "folder" / "page.html"
    target_file.parent.mkdir()
    target_file.write_text("content", encoding="utf-8")

    html = (
        '<a href="folder/page.html">page</a>'
        '<a href="https://example.com">external</a>'
        '<a href="#top">top</a>'
    )

    # 結合モード (flat=False)
    result_merged = rewrite_local_links(html, current_file, base_dir, flat=False)
    assert 'href="folder/page.md"' in result_merged
    assert 'href="https://example.com"' in result_merged
    assert 'href="#top"' in result_merged

    # 個別保存モード (flat=True)
    result_flat = rewrite_local_links(html, current_file, base_dir, flat=True)
    assert 'href="folder_page.md"' in result_flat


@pytest.mark.parametrize("workers", [None, 1, 4])
def test_process_local_directory(tmp_path: Path, workers: int | None) -> None:
    """
    ディレクトリ内の HTML ファイルが探索され、成果物へ集約されることを検証する。

    [事前条件 (Given)]
    一時ディレクトリ配下に HTML ファイルが階層的に配置されており、
    一部に HTML 以外のファイルも混在している状態。

    [実行 (When)]
    process_local_directory を結合モード（no_merge=False）で実行する。

    [検証 (Then)]
    - 指定した出力ファイルが作成されること。
    - 出力ファイル内に、すべての HTML ファイルの内容およびツリー構造が含まれること。
    - HTML 以外のファイル（.txtなど）の内容は含まれないこと。
    """
    base_dir = tmp_path / "site"
    base_dir.mkdir()

    (base_dir / "index.html").write_text(
        "<html><main><h1>Index</h1></main></html>", encoding="utf-8"
    )
    folder = base_dir / "blog"
    folder.mkdir()
    (folder / "post.html").write_text(
        "<html><main><h1>Post</h1></main></html>", encoding="utf-8"
    )
    (base_dir / "readme.txt").write_text("Ignored txt", encoding="utf-8")

    output_file = tmp_path / "out.md"
    config = CrawlConfig(
        start_url=None,
        limit_prefix=None,
        local_path=str(base_dir),
        output=str(output_file),
        size_limit=SizeLimit(value=10_000, unit="bytes", raw="10KB"),
        as_html=False,
        no_merge=False,
        delay_min=0.0,
        delay_max=0.0,
        workers=workers,
    )

    process_local_directory(config)

    assert output_file.exists()
    content = output_file.read_text(encoding="utf-8")
    assert "Directory structure:" in content
    assert "index.md" in content
    assert "blog/post.md" in content
    assert "# Index" in content
    assert "# Post" in content
    assert "Ignored txt" not in content


@pytest.mark.parametrize("workers", [None, 1, 4])
def test_process_local_file_links(tmp_path: Path, workers: int | None) -> None:
    """
    起点 HTML からリンクされているローカル HTML のみが再帰追跡されることを検証する。

    [事前条件 (Given)]
    起点となる index.html、そこからリンクされている page1.html、
    リンクされていない孤立した orphan.html が存在する状態。

    [実行 (When)]
    process_local_file_links を結合モードで実行する。

    [検証 (Then)]
    - 起点 HTML とリンク先の HTML のみが出力ファイルに含まれること。
    - リンクされていない孤立した HTML は出力ファイルに含まれないこと。
    """
    base_dir = tmp_path / "site"
    base_dir.mkdir()

    (base_dir / "index.html").write_text(
        '<html><main><h1>Index</h1><a href="page1.html">Link</a></main></html>',
        encoding="utf-8",
    )
    (base_dir / "page1.html").write_text(
        "<html><main><h1>Page 1</h1></main></html>", encoding="utf-8"
    )
    (base_dir / "orphan.html").write_text(
        "<html><main><h1>Orphan</h1></main></html>", encoding="utf-8"
    )

    output_file = tmp_path / "out.md"
    config = CrawlConfig(
        start_url=None,
        limit_prefix=None,
        local_path=str(base_dir / "index.html"),
        output=str(output_file),
        size_limit=SizeLimit(value=10_000, unit="bytes", raw="10KB"),
        as_html=False,
        no_merge=False,
        delay_min=0.0,
        delay_max=0.0,
        workers=workers,
    )

    process_local_file_links(config)

    assert output_file.exists()
    content = output_file.read_text(encoding="utf-8")
    assert "# Index" in content
    assert "# Page 1" in content
    assert "# Orphan" not in content
