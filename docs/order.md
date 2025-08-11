了解。ClaudeCode に渡す「指示文（プロンプト）」をそのまま貼れる形で用意しました。
そのままコピペして実行してください。必要なら後半の「補足メモ」であなたの環境向けの微調整点も付けています。

---

# 指示文（ClaudeCode へのプロンプト）

あなたは**シニアPythonエンジニア**です。
以下の要件を満たす **単一の Python スクリプト**（原則1ファイル）と、最小限の補助ファイル（`requirements.txt` など）が欲しいです。Mac（fish）で動かします。

## 目的

YouTube の動画（ゆる言語ラジオ想定）について：

1. `youtube-transcript-api` で文字起こしを取得しローカル保存
2. 生成AI（Anthropic Claude もしくは OpenAI）で **日本語の要約**を作成しローカル保存
3. 400件以上でも**再実行で差分だけ**処理できる実用ツール化

## 入出力・保存仕様（厳守）

- 入力は以下のいずれかを受け取れること（相互排他でOK）

  - `--channel-id <CHANNEL_ID>`（例: UCxxxx…）→ Atom フィード `https://www.youtube.com/feeds/videos.xml?channel_id=...` を使って最新N本を取得
  - `--playlist-id <PLAYLIST_ID>`（Uploads プレイリストなど）→ 同様に最新N本を取得
  - `--video-ids-file <PATH>`（1行1ID/URL）→ その全件を処理
- 取得対象件数を `--max-videos N` で制御（デフォルト 50、上限なし可）
- 出力ディレクトリ（例 `--outdir ./out`）配下に以下を保存

  - `transcripts/{video_id}.json`（セグメント配列：start, duration, text）
  - `transcripts/{video_id}.txt`（連結テキスト）
  - `summaries/{video_id}.json`（要約結果：後述のJSONスキーマ）
  - `summaries/{video_id}.md`（人が読む用のMarkdown）
  - 進捗集計 `index.csv`（追記/更新；列：video\_id, title, url, published\_at, lang, transcript\_chars, tokens\_estimate, summary\_status, error）
- **既存ファイルがあればスキップ**。`--force` 指定時は再生成。
- ログは標準出力＋`--log-file`指定でファイルも可。進捗は `tqdm`。

## 文字起こし取得（youtube-transcript-api）

- 優先言語は `--languages ja,ja-JP,en` などのCSV指定（デフォルトは `ja,ja-JP,en`）。
- `list_transcripts()` の結果から最初に合致する言語を選択。なければ `translated` で `ja` 入手を試行できるように。
- 取得できない場合は `index.csv` に `summary_status=TRANSCRIPT_UNAVAILABLE` とエラー理由を記録、処理は継続。
- テキスト連結時、`[音楽]` 等の角括弧メタは `--clean-tags` 指定時に除去（デフォルトはそのまま）。

## 公開日の取得

- `--channel-id` または `--playlist-id` 指定時：Atomフィードから `published` を取得。
- `--video-ids-file` 指定時：可能ならフィードを叩いて補完（難しければ未設定可）。
- `published_at` は ISO8601（UTC）で保存。URL は `https://www.youtube.com/watch?v={video_id}`。

## 要約生成（生成AI）

- プロバイダ切替：`--provider anthropic|openai`（デフォルト `anthropic`）
- モデル指定：`--model`（例：`claude-3-5-sonnet-latest` / `gpt-4.1-mini` など）。
- APIキーは環境変数：

  - Anthropic → `ANTHROPIC_API_KEY`
  - OpenAI → `OPENAI_API_KEY`
- トークン制限対策：**マップ→リデュース**方式

  - チャンクサイズは `--chunk-chars`（デフォルト 6000 文字）と `--chunk-overlap`（デフォルト 300）
  - 各チャンクを「要点ブレット」に要約 → 最後に統合要約を生成
- **出力フォーマット（JSON）**（`summaries/{video_id}.json`）：

  ```json
  {
    "video_id": "...",
    "title": "...",
    "url": "https://www.youtube.com/watch?v=...",
    "published_at": "2025-07-01T09:45:00Z",
    "language": "ja",
    "summary": "本文1段落の要約(TL;DR)。",
    "highlights": ["● 箇条書き最大10件まで", "..."],
    "new_insights": ["● 会話から得られる新規示唆 3〜5件", "..."],
    "notable_quotes": [{"t":"00:05:12","text":"引用..."}, {"t":"00:18:40","text":"..."}],
    "tokens_estimate": 1234
  }
  ```

- **Markdown 版**（`summaries/{video_id}.md`）：

  ```markdown
  # {title}
  - 公開日: {YYYY-MM-DD}
  - URL: https://www.youtube.com/watch?v=...
  - 言語: {language}

  ## TL;DR
  （1段落）

  ## Highlights
  - 箇条書き

  ## 新たな気づき
  - 箇条書き（会話の文脈からの示唆）

  ## 印象的な引用
  - [00:05:12] 〜
  - [00:18:40] 〜
  ```

- **日本語の要約プロンプト**はスクリプト内で用意：

  - 「文章だけでなく**会話の相互作用**から得られる示唆を抽出」
  - 出力は上記JSON項目を満たすこと
  - 文字数上限は `--summary-max-tokens` で調整可能

## CLI 仕様

- 例：

  ```bash
  # 例1: チャンネルIDの最新100件を処理（Anthropic）
  python yt_summary.py \
    --channel-id UCxxxxxxxxxxxxxxxx \
    --max-videos 100 \
    --outdir ./out \
    --provider anthropic \
    --model claude-3-5-sonnet-latest

  # 例2: 動画ID一覧ファイルから（OpenAIで要約、タグ除去）
  python yt_summary.py \
    --video-ids-file ./video_ids.txt \
    --outdir ./out \
    --provider openai \
    --model gpt-4.1-mini \
    --clean-tags

  # 例3: 再実行で差分のみ。強制再計算するなら --force
  python yt_summary.py --channel-id UCxxxx --outdir ./out
  ```

- fish 用環境変数例：

  ```fish
  set -x -g ANTHROPIC_API_KEY "sk-ant-..."
  set -x -g OPENAI_API_KEY "sk-proj-..."
  ```

## 実装要件

- 言語: Python 3.11 以上、型ヒント・docstring 付き。
- 主要ライブラリ：`youtube-transcript-api`, `httpx` or `requests`, `tqdm`, `pydantic`（任意）, `anthropic` / `openai`（選択的）。標準ライブラリでXMLパース（`xml.etree`）可。
- 例外処理：`tenacity` などで**指数バックオフ**リトライ（429/5xx時）。
- レート制御：`--rps`（デフォルト 0.8 rps）で `sleep`。
- 文字数・トークン見積：概算（日本語=2〜3文字/トークン換算）で `tokens_estimate` を保存。
- **再実行安全**：既存ファイルを見て自動スキップ。`--force` で上書き。
- **ユニットテストは不要**だが、`--dry-run` で計画と件数のみ出力する実行パスを実装。
- 出力`index.csv` は**逐次更新**（行の upsert）。
- ログはINFO/ERROR。処理失敗時も次の動画へ進む。

## 受け入れ基準（チェックリスト）

- [ ] `--channel-id` / `--playlist-id` / `--video-ids-file` のいずれでも動く
- [ ] 文字起こし・要約が所定の場所へ保存される
- [ ] 取得不可動画はスキップし `index.csv` に理由付きで記録
- [ ] 大量件数でも再実行時に差分のみ処理
- [ ] マップ→リデュース要約が動作し、JSON/MD の体裁が崩れない
- [ ] エラー時に落ちず、次の動画へ継続
- [ ] コマンド例を実行すればそのまま動く（`requirements.txt` あり）

## 納品物

- `yt_summary.py`（単一スクリプト）
- `requirements.txt`
- `README.md`（セットアップと使い方。fish の例と注意点を明記）

---

## 補足メモ（あなた向け）

- **公開日**はYouTube Data APIなしで、**チャンネル/プレイリストの Atom フィード**から取る形にしています（鍵不要）。
- **大量件数**：まず `--max-videos` で限定して試し、動作確認後に全件へ。
- **Claude優先**：`--provider anthropic` をデフォルトにしておくとClaudeCodeで作りやすいです。
- 要らなければ `pydantic` は外してOK。依存を少なめにするなら `requests` + 標準 `dataclasses` でも可。

修正や追加の条件があれば、そのままこのプロンプトに追記して再実行してください。
