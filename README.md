# YouTube Video Transcript and Summary Tool

YouTube動画の文字起こしを取得し、AI（Claude/OpenAI）で日本語要約を生成するツールです。チャンネル内の全動画を一括処理する機能も備えており、大量の動画を効率的に処理できます。

## 主な機能

- 📺 **チャンネル全動画の一括処理** - チャンネル内の全動画をCSVに出力し、一括で文字起こし・要約
- 🎯 YouTube チャンネル/プレイリストから動画を自動取得
- 📝 文字起こしの取得（日本語優先、多言語対応）
- 🤖 AI による日本語要約生成（会話の文脈を考慮）
- 🔄 差分管理による効率的な再実行
- 📊 マップリデュース方式による長時間動画対応
- 🛡️ IPブロック回避機能（Cookie認証、yt-dlp対応）

## セットアップ

### 1. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

### 2. 環境変数の設定

#### 方法1: .envファイルを使用（推奨）

`.env.example`をコピーして`.env`ファイルを作成し、APIキーを設定します：

```bash
# .envファイルを作成
cp .env.example .env

# .envファイルを編集してAPIキーを設定
# ANTHROPIC_API_KEY=sk-ant-xxxxx
# OPENAI_API_KEY=sk-proj-xxxxx
# YOUTUBE_API_KEY=AIzaSyxxxxx（オプション）
```

#### 方法2: 環境変数を直接設定

```bash
# bash/zsh の場合
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-proj-..."
export YOUTUBE_API_KEY="AIza..."  # オプション

# fish シェルの場合
set -x -g ANTHROPIC_API_KEY "sk-ant-..."
set -x -g OPENAI_API_KEY "sk-proj-..."
set -x -g YOUTUBE_API_KEY "AIza..."  # オプション
```

## クイックスタート

### 🎬 チャンネル全動画を一括処理（新機能）

```bash
# 方法1: 統合スクリプトで一括実行
python process_channel.py <channel_id> --max-videos 10

# 方法2: チャンネルURLから処理
python process_channel.py --from-url "https://www.youtube.com/@GoogleDevelopers" --max-videos 5

# 方法3: 既存のindex.csvから処理を再開
python process_channel.py --use-existing-csv index.csv
```

## 使用方法

### 📋 チャンネル動画一覧の取得（channel_index.py）

チャンネル内の全動画情報をCSVファイルに出力します：

```bash
# チャンネルIDから動画一覧を取得
python channel_index.py <channel_id>

# チャンネルURLから自動的にIDを取得
python channel_index.py --from-url "https://www.youtube.com/@GoogleDevelopers"

# YouTube Data APIで全動画を取得（要APIキー）
export YOUTUBE_API_KEY="your-api-key"
python channel_index.py <channel_id> --max-pages 20
```

出力されるCSV形式：
- video_id: 動画ID
- title: タイトル
- url: 動画URL
- published_at: 公開日時
- description: 説明文（最初の500文字）
- channel_name: チャンネル名
- channel_id: チャンネルID

### 🤖 文字起こし・要約処理（yt_summary.py）

#### チャンネルの最新動画を処理（Anthropic Claude使用）

```bash
python yt_summary.py \
  --channel-id UCxxxxxxxxxxxxxxxx \
  --max-videos 100 \
  --outdir ./out \
  --provider anthropic \
  --model claude-3-5-sonnet-latest
```

#### CSVファイルから処理（新機能）

```bash
# channel_index.pyで生成したCSVを使用
python yt_summary.py \
  --video-ids-file index.csv \
  --max-videos 50 \
  --outdir ./out
```

#### プレイリストから処理

```bash
python yt_summary.py \
  --playlist-id PLxxxxxxxxxxxxxxxx \
  --max-videos 50 \
  --outdir ./out
```

#### 動画IDリストファイルから処理（OpenAI使用）

```bash
# video_ids.txt: 1行1動画ID or URL
python yt_summary.py \
  --video-ids-file ./video_ids.txt \
  --outdir ./out \
  --provider openai \
  --model gpt-4o-mini \
  --clean-tags  # [音楽]等のタグを除去
```

### 🔄 一括処理スクリプト（process_channel.py）

チャンネル動画の取得から文字起こし・要約まで一括で実行：

```bash
# 基本的な使い方
python process_channel.py <channel_id>

# 詳細なオプション
python process_channel.py <channel_id> \
  --max-videos 20 \
  --provider openai \
  --model gpt-4o-mini \
  --clean-tags \
  --use-ytdlp \
  --cookies-file cookies.txt
```

### 差分処理と再実行

既に処理済みの動画はスキップされます：

```bash
# 2回目の実行では新規動画のみ処理
python yt_summary.py --channel-id UCxxxx --outdir ./out
```

強制的に再処理する場合：

```bash
python yt_summary.py --channel-id UCxxxx --outdir ./out --force
```

### ドライラン（実行計画の確認）

```bash
python yt_summary.py --channel-id UCxxxx --dry-run
```

## 出力ファイル構成

```plaintext
out/
├── transcripts/
│   ├── {video_id}.json    # セグメント形式の文字起こし
│   └── {video_id}.txt     # 連結されたテキスト
├── summaries/
│   ├── {video_id}.json    # 構造化された要約データ
│   └── {video_id}.md      # 人間が読むためのMarkdown
└── index.csv              # 処理状況の一覧
```

### 要約JSONの構造

```json
{
  "video_id": "...",
  "title": "動画タイトル",
  "url": "https://www.youtube.com/watch?v=...",
  "published_at": "2025-07-01T09:45:00Z",
  "language": "ja",
  "summary": "1段落の要約（TL;DR）",
  "highlights": [
    "重要ポイント1",
    "重要ポイント2"
  ],
  "new_insights": [
    "会話から得られる新しい気づき1",
    "会話から得られる新しい気づき2"
  ],
  "notable_quotes": [
    {"t": "00:05:12", "text": "印象的な引用1"},
    {"t": "00:18:40", "text": "印象的な引用2"}
  ],
  "tokens_estimate": 1234
}
```

## 環境変数（.env）設定

`.env`ファイルで以下の設定をカスタマイズできます：

```bash
# AI設定
AI_PROVIDER=anthropic          # AI プロバイダー (anthropic/openai)
AI_MODEL=claude-3-5-sonnet-latest  # 使用モデル

# 処理設定
MAX_VIDEOS=50                  # 最大処理動画数
OUTPUT_DIR=./out               # 出力ディレクトリ
CHUNK_SIZE=6000                # チャンクサイズ
CHUNK_OVERLAP=300              # チャンク重複
REQUESTS_PER_SECOND=0.8        # API呼び出しレート制限

# 言語設定
LANGUAGES=ja,ja-JP,en          # 優先言語順

# オプション設定
USE_YTDLP=false               # yt-dlp使用フラグ
CLEAN_TAGS=false              # タグクリーンフラグ
```

## オプション一覧

### 入力ソース（いずれか1つを指定）

- `--channel-id`: YouTubeチャンネルID
- `--playlist-id`: YouTubeプレイリストID
- `--video-ids-file`: 動画ID/URLのリストファイル（テキストまたはCSV）

### 処理設定

- `--max-videos N`: 処理する最大動画数（デフォルト: 50）
- `--outdir DIR`: 出力ディレクトリ（デフォルト: ./out）
- `--force`: 既存ファイルを強制的に再生成
- `--dry-run`: 実行計画のみ表示（実際の処理は行わない）

### 文字起こし設定

- `--languages`: 優先言語リスト（デフォルト: ja,ja-JP,en）
- `--clean-tags`: [音楽]等のメタタグを除去

### AI設定

- `--provider`: AIプロバイダー（anthropic/openai、デフォルト: anthropic）
- `--model`: 使用モデル（デフォルト: claude-3-5-sonnet-latest）
- `--chunk-chars`: チャンクサイズ（デフォルト: 6000）
- `--chunk-overlap`: チャンク重複（デフォルト: 300）

### その他

- `--rps`: API呼び出しレート制限（デフォルト: 0.8 req/sec）
- `--log-file`: ログファイルパス
- `--proxy`: HTTP/HTTPSプロキシURL（例: <http://proxy.example.com:8080>）
- `--cookies-file`: YouTube認証用のcookies.txtファイルパス
- `--use-ytdlp`: yt-dlpを使用して文字起こしを取得（IPブロック回避に有効）

## トラブルシューティング

### 文字起こしが取得できない場合

- 動画に字幕が設定されていない可能性があります
- `--languages` オプションで他の言語を試してください

### IP ブロック（IpBlocked エラー）が発生する場合

YouTubeがIPをブロックしている可能性があります。以下の対策を試してください：

1. **ブラウザのCookieを使用（最も効果的）**

   ブラウザでYouTubeにログインした状態のCookieを使用することで、認証済みとして扱われます：

   ```bash
   # 方法1: 付属のスクリプトで自動抽出
   python extract_cookies.py --browser chrome --output cookies.txt

   # 方法2: browser_cookie3を使用（要インストール）
   pip install browser_cookie3
   python extract_cookies.py --output cookies.txt

   # Cookieを使用して実行
   python yt_summary.py \
     --channel-id UCpLu0KjNy616-E95gPx7LZg \
     --max-videos 10 \
     --outdir ./out \
     --provider openai \
     --model gpt-4o \
     --cookies-file cookies.txt \
     --use-ytdlp
   ```

2. **yt-dlp を使用**

   yt-dlpはより堅牢で、IPブロックを回避しやすいです：

   ```bash
   # yt-dlpをインストール
   pip install yt-dlp

   # --use-ytdlp オプションを追加
   python yt_summary.py \
     --channel-id UCpLu0KjNy616-E95gPx7LZg \
     --max-videos 10 \
     --outdir ./out \
     --use-ytdlp
   ```

   ※ IPブロックエラーが発生した場合、自動的にyt-dlpにフォールバックします

3. **レート制限を調整**

   ```bash
   python yt_summary.py --channel-id UCxxxx --rps 0.3
   ```

4. **プロキシを使用**

   ```bash
   python yt_summary.py --channel-id UCxxxx --proxy http://proxy.example.com:8080
   ```

5. **時間を置いて再実行**
   - 数時間〜1日待ってから再実行

### API エラーが発生する場合

- API キーが正しく設定されているか確認してください
- レート制限に達している場合は `--rps` を小さくしてください

### メモリ不足の場合

- `--chunk-chars` を小さくして、チャンクサイズを調整してください

## ツール一覧

| ツール | 説明 | 主な用途 |
|--------|------|----------|
| `yt_summary.py` | メインの文字起こし・要約ツール | 動画の文字起こしと要約生成 |
| `channel_index.py` | チャンネル動画一覧取得ツール | チャンネル内の全動画をCSVに出力 |
| `process_channel.py` | 一括処理統合ツール | チャンネル全動画の自動処理 |
| `extract_cookies.py` | Cookie抽出ツール | IPブロック回避用の認証情報取得 |
| `get_channel_id.py` | チャンネルID取得ツール | URLからチャンネルIDを抽出 |

## 注意事項

- APIの利用料金が発生します（特に大量処理時）
- 初回実行時は `--max-videos` を小さくして動作確認することを推奨
- `index.csv` で処理状況を確認できます
- YouTube Data APIを使用する場合は、[Google Cloud Console](https://console.cloud.google.com/)でAPIキーを取得してください

## ライセンス

MIT License
