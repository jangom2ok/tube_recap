#!/usr/bin/env python3
"""
Channel Video Processing Tool

チャンネルの動画を一括で文字起こし・要約処理するツール
"""

import argparse
import subprocess
import sys
from pathlib import Path
import time
import logging


def setup_logging(verbose: bool = False):
    """ロギング設定"""
    log_level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger(__name__)


def main():
    """メインエントリーポイント"""
    parser = argparse.ArgumentParser(
        description='チャンネルの動画を一括で文字起こし・要約処理',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # チャンネルIDから全動画を処理（2ステップ実行）
  python process_channel.py UC_x5XG1OV2P6uZZ5FSM9Ttw

  # チャンネルURLから処理
  python process_channel.py --from-url "https://www.youtube.com/@GoogleDevelopers"

  # 既存のindex.csvを使用して処理
  python process_channel.py --use-existing-csv index.csv

  # 処理数を制限
  python process_channel.py UC_x5XG1OV2P6uZZ5FSM9Ttw --max-videos 10

  # AI プロバイダーとモデルを指定
  python process_channel.py UC_x5XG1OV2P6uZZ5FSM9Ttw --provider openai --model gpt-4-turbo

  # プロキシとCookies使用（IPブロック回避）
  python process_channel.py UC_x5XG1OV2P6uZZ5FSM9Ttw --proxy http://proxy:8080 --cookies-file cookies.txt

注意:
  - 環境変数 ANTHROPIC_API_KEY または OPENAI_API_KEY が必要
  - --use-existing-csv を使わない場合は、まず channel_index.py でCSVを生成してから処理
        """
    )

    # 入力オプション
    parser.add_argument('channel_input', nargs='?',
                       help='YouTube チャンネルID (UC...) またはチャンネルURL（--from-url使用時）')
    parser.add_argument('--from-url', action='store_true',
                       help='入力をチャンネルURLとして処理')
    parser.add_argument('--use-existing-csv',
                       help='既存のCSVファイルを使用（channel_inputは不要）')

    # 処理オプション
    parser.add_argument('--outdir', default='./out',
                       help='出力ディレクトリ (default: ./out)')
    parser.add_argument('--max-videos', type=int,
                       help='処理する最大動画数')
    parser.add_argument('--force', action='store_true',
                       help='既存のファイルを強制的に再生成')
    parser.add_argument('--dry-run', action='store_true',
                       help='実際の処理を行わずに処理対象を表示')

    # 文字起こしオプション
    parser.add_argument('--languages', default='ja,ja-JP,en',
                       help='優先言語順 (default: ja,ja-JP,en)')
    parser.add_argument('--clean-tags', action='store_true',
                       help='文字起こしから[タグ]を削除')
    parser.add_argument('--use-ytdlp', action='store_true',
                       help='yt-dlpを使用（IPブロック回避に有効）')

    # AI要約オプション
    parser.add_argument('--provider', choices=['anthropic', 'openai'],
                       default='anthropic', help='AIプロバイダー (default: anthropic)')
    parser.add_argument('--model', default='claude-3-5-sonnet-latest',
                       help='モデル名 (default: claude-3-5-sonnet-latest)')
    parser.add_argument('--chunk-chars', type=int, default=6000,
                       help='map-reduce時のチャンクサイズ (default: 6000)')

    # ネットワークオプション
    parser.add_argument('--proxy', help='HTTP/HTTPSプロキシURL')
    parser.add_argument('--cookies-file', help='YouTube認証用cookies.txtファイル')
    parser.add_argument('--rps', type=float, default=0.8,
                       help='リクエスト/秒の制限 (default: 0.8)')

    # その他
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='詳細な出力を表示')
    parser.add_argument('--csv-output', default='index.csv',
                       help='CSVファイル名 (default: index.csv)')

    args = parser.parse_args()
    logger = setup_logging(args.verbose)

    # 引数チェック
    if not args.use_existing_csv and not args.channel_input:
        parser.error("channel_input または --use-existing-csv が必要です")

    # CSVファイルのパス
    csv_file = Path(args.use_existing_csv if args.use_existing_csv else args.csv_output)

    # ステップ1: CSVファイル生成（必要な場合）
    if not args.use_existing_csv:
        logger.info("Step 1: チャンネル動画リストを取得中...")

        # channel_index.py を実行
        cmd = ['python', 'channel_index.py']

        if args.from_url:
            cmd.append('--from-url')
        cmd.append(args.channel_input)

        cmd.extend(['-o', str(csv_file)])

        if args.verbose:
            cmd.append('-v')

        logger.info(f"実行コマンド: {' '.join(cmd)}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            if args.verbose:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
        except subprocess.CalledProcessError as e:
            logger.error(f"channel_index.py の実行に失敗: {e}")
            if e.stdout:
                print(e.stdout)
            if e.stderr:
                print(e.stderr, file=sys.stderr)
            sys.exit(1)

        logger.info(f"CSVファイルを生成: {csv_file}")
        time.sleep(1)  # 少し待機

    # CSVファイルの存在確認
    if not csv_file.exists():
        logger.error(f"CSVファイルが見つかりません: {csv_file}")
        sys.exit(1)

    # ステップ2: 文字起こし・要約処理
    logger.info("Step 2: 文字起こしと要約処理を実行中...")

    # yt_summary.py を実行
    cmd = ['python', 'yt_summary.py', '--video-ids-file', str(csv_file)]

    # 出力ディレクトリ
    cmd.extend(['--outdir', args.outdir])

    # 処理数制限
    if args.max_videos:
        cmd.extend(['--max-videos', str(args.max_videos)])

    # 言語設定
    cmd.extend(['--languages', args.languages])

    # タグクリーン
    if args.clean_tags:
        cmd.append('--clean-tags')

    # AI設定
    cmd.extend(['--provider', args.provider])
    cmd.extend(['--model', args.model])
    cmd.extend(['--chunk-chars', str(args.chunk_chars)])

    # ネットワーク設定
    if args.proxy:
        cmd.extend(['--proxy', args.proxy])
    if args.cookies_file:
        cmd.extend(['--cookies-file', args.cookies_file])
    if args.use_ytdlp:
        cmd.append('--use-ytdlp')
    cmd.extend(['--rps', str(args.rps)])

    # その他のオプション
    if args.force:
        cmd.append('--force')
    if args.dry_run:
        cmd.append('--dry-run')

    logger.info(f"実行コマンド: {' '.join(cmd)}")

    process = None
    try:
        # yt_summary.py はリアルタイムで出力を表示
        process = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)
        process.wait()

        if process.returncode != 0:
            logger.error(f"yt_summary.py がエラーコード {process.returncode} で終了")
            sys.exit(process.returncode)

    except KeyboardInterrupt:
        logger.info("処理を中断しました")
        if process is not None:
            process.terminate()
        sys.exit(130)
    except Exception as e:
        logger.error(f"処理中にエラーが発生: {e}")
        sys.exit(1)

    logger.info("処理が完了しました")
    logger.info(f"結果は {args.outdir} ディレクトリに保存されています")


if __name__ == '__main__':
    main()

