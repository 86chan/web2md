# web2md

Webサイトのドキュメントをクロールし、1つのテキストファイル（または分割されたファイル）に集約するツールです。集約後のファイル形式は [gitingest](https://github.com/cyclotomas/gitingest) 風の構成になっており、LLMへのコンテキスト注入に最適化されています。

## 主な機能

- **Webクロール**: 指定したURLを起点に、同一ドメイン内の関連ドキュメントを再帰的に辿ります
- **Markdown変換**: `markdownify` を使用して、HTMLから高品質なMarkdownを抽出
- **ノイズ除去**: `main` や `article` 要素を優先的に抽出して不要な要素を省きます
- **内部リンクの成果物向け変換**: クロール対象内のページへのリンクを、成果物内のファイルパス（結合時の `File:` 見出し／`--no-merge` 時の個別ファイル名）に自動で書き換えます。外部サイトや非テキスト、同一ページ内アンカー（`#...`）へのリンクは変更しません
- **自動分割**: LLMのコンテキスト制限を考慮し、指定したファイルサイズに合わせて自動的に分割出力
- **柔軟な出力形式**:
    - 1ファイルへの結合出力（デフォルト）
    - HTMLとしてそのまま保存
    - Markdownとして個別のファイルに保存
- **ディレクトリ構造の可視化**: ファイルのツリー構造を冒頭に自動含めます

## 使用方法

### インストール

```bash
pip install -r requirements.txt
```

### 実行

```bash
python scrip.py [-u <開始URL> | -l <ローカルパス>] -o <出力ファイル名> [オプション]
```

#### オプション

| オプション | デフォルト | 説明 |
| --- | --- | --- |
| `-u, --url` | `-l` といずれか**必須** | クロールを開始するURL |
| `-l, --local-path` | `-u` といずれか**必須** | ローカルのHTMLファイル（リンク追跡）またはディレクトリ（再帰探索）のパス |
| `-p, --prefix` | URLと同等 | クロールを許可するURLの接頭辞（制限用、Webモードのみ有効） |
| `-o, --output` | **必須** | 出力ファイル名（`--no-merge`時や`--html`時は出力ディレクトリ名） |
| `-m, --max-size` | `1MB` | 1ファイルあたりの最大サイズ（例: `500KB`, `1.5MB`, `1048576`） |
| `-d, --delay` | `0.5` | リクエスト間ランダム待機の下限秒数（サーバ負荷軽減、Webモードのみ有効） |
| `--delay-max` | `1.7` | リクエスト間ランダム待機の上限秒数（Webモードのみ有効） |
| `--html` | `False` | Markdown変換を行わず生のHTMLとして保存 |
| `--no-merge` | `False` | 結合せず各ページを個別のMarkdownファイルとして保存 |

#### 実行例

```bash
# Webクロール: 1MB以内のファイルに分割して集約（デフォルト）
python scrip.py -u https://example.com/docs -o docs.md

# Webクロール: クロール範囲を特定のディレクトリ以下に制限
python scrip.py -u https://example.com/docs -p https://example.com/docs/api -o api.md

# ローカル: 指定ディレクトリ内のすべてのHTMLファイルを再帰探索して集約
python scrip.py -l ./local_site -o local_docs.md

# ローカル: 起点HTMLからリンクされているファイルを再帰追跡して個別に保存
python scrip.py -l ./local_site/index.html -o output_dir --no-merge

# 500KB ごとに分割
python scrip.py -u https://example.com/docs -o split.md -m 500KB
```

## 出力形式

集約されたファイルは以下の構成になります：

```text
.
├── folder
│   └── subfolder
│       └── file.md
├── index.md
└── another.md

================================================
File: index.md
================================================
[Markdown内容]

================================================
File: folder/subfolder/file.md
================================================
[Markdown内容]
...
```
※ 最大サイズを超えた場合は、`output_part_1.md`, `output_part_2.md`, ... と分割されます。

## 依存関係

- `requests`: HTTPリクエスト
- `beautifulsoup4`: HTMLパース
- `markdownify`: HTML → Markdown 変換
- `mdformat`: Markdown フォーマット整形
- `trafilatura` (merge.pyで使用): 高精度な本文抽出

## 開発

品質チェック（Lint / 型 / テスト）は以下で実行できます。

```bash
# 開発用依存のインストール
pip install -r requirements-dev.txt

# Lint・フォーマット確認
ruff check .
ruff format --check .

# 静的型チェック（mypy strict）
mypy .

# テスト
pytest
```

CI（GitHub Actions）でも push / pull request 時に上記を Python 3.11 / 3.12 で自動実行します。

---
このプロジェクトは、LLMへのコンテキスト注入用データセット作成を支援するために作成されました。
