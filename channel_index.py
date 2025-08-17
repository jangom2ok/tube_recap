#!/usr/bin/env python3
"""
YouTube Channel Video Index Generator

チャンネル内の全動画情報をCSVファイルに出力するツール
"""

import argparse
import csv
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict

import httpx
import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


@dataclass
class VideoMetadata:
    """動画メタデータ"""
    video_id: str
    title: str
    url: str
    published_at: str
    description: str = ""
    duration: str = ""
    views: str = ""
    channel_name: str = ""
    channel_id: str = ""


class ChannelIndexGenerator:
    """チャンネル動画インデックス生成ツール"""

    def __init__(self, channel_id: str, output_file: str = "index.csv",
                 max_pages: int = 10, verbose: bool = False):
        self.channel_id = channel_id
        self.output_file = Path(output_file)
        self.max_pages = max_pages
        self.verbose = verbose
        self.videos: List[VideoMetadata] = []

        # ロギング設定
        log_level = logging.INFO if verbose else logging.WARNING
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)

    def get_channel_id_from_url(self, url: str) -> Optional[str]:
        """URLからチャンネルIDを取得"""
        try:
            response = requests.get(url, timeout=30)
            html = response.text
            match = re.search(r'channelId":"(UC[0-9A-Za-z_-]+)"', html)
            if match:
                return match.group(1)
        except Exception as e:
            self.logger.error(f"Failed to get channel ID from URL: {e}")
        return None

    def fetch_channel_feed(self) -> Optional[str]:
        """チャンネルのAtomフィードを取得"""
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={self.channel_id}"
        try:
            response = httpx.get(feed_url, timeout=30)
            response.raise_for_status()
            return response.text
        except Exception as e:
            self.logger.error(f"Failed to fetch channel feed: {e}")
            return None

    def parse_feed(self, feed_content: str) -> List[VideoMetadata]:
        """Atomフィードをパースして動画情報を抽出"""
        videos: List[VideoMetadata] = []
        try:
            root = ET.fromstring(feed_content)

            # 名前空間定義
            ns = {
                'atom': 'http://www.w3.org/2005/Atom',
                'yt': 'http://www.youtube.com/xml/schemas/2015',
                'media': 'http://search.yahoo.com/mrss/'
            }

            # チャンネル情報を取得
            channel_name = ""
            author_elem = root.find('atom:author/atom:name', ns)
            if author_elem is not None:
                channel_name = author_elem.text or ""

            # 各エントリー（動画）を処理
            for entry in root.findall('atom:entry', ns):
                video_id_elem = entry.find('yt:videoId', ns)
                if video_id_elem is None:
                    continue

                video_id = video_id_elem.text or ""

                # タイトル
                title_elem = entry.find('atom:title', ns)
                title = title_elem.text if title_elem is not None else ""

                # 公開日時
                published_elem = entry.find('atom:published', ns)
                published = published_elem.text if published_elem is not None else ""

                # 説明文（media:description）
                description_elem = entry.find('media:group/media:description', ns)
                description = description_elem.text if description_elem is not None else ""

                # 再生回数と評価
                stats_elem = entry.find('media:group/media:community', ns)
                views = ""
                if stats_elem is not None:
                    statistics = stats_elem.find('media:statistics', ns)
                    if statistics is not None:
                        views = statistics.get('views', '')

                video = VideoMetadata(
                    video_id=video_id,
                    title=title or "",
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    published_at=published or "",
                    description=description[:500] if description else "",  # 説明文は最初の500文字まで
                    duration="",  # フィードには含まれない
                    views=views,
                    channel_name=channel_name,
                    channel_id=self.channel_id
                )
                videos.append(video)

        except Exception as e:
            self.logger.error(f"Failed to parse feed: {e}")

        return videos

    def fetch_all_videos_via_api(self) -> List[VideoMetadata]:
        """YouTube Data APIを使用して全動画を取得（APIキーが必要）"""
        api_key = os.getenv('YOUTUBE_API_KEY')
        if not api_key:
            self.logger.warning("YOUTUBE_API_KEY not found in environment or .env file. Using feed method (limited to recent videos).")
            return []

        videos: List[VideoMetadata] = []
        next_page_token = None
        page_count = 0

        uploads_playlist_id = ""
        channel_name = ""

        try:
            while page_count < self.max_pages:
                # チャンネルのアップロード済み動画プレイリストIDを取得
                if page_count == 0:
                    channel_url = "https://www.googleapis.com/youtube/v3/channels"
                    channel_params = {
                        'part': 'contentDetails,snippet',
                        'id': self.channel_id,
                        'key': api_key
                    }
                    response = requests.get(channel_url, params=channel_params, timeout=30)
                    data = response.json()
                    
                    # デバッグ情報を追加
                    if 'error' in data:
                        self.logger.error(f"API error: {data['error']}")
                        return []

                    if not data.get('items'):
                        self.logger.error(f"Channel not found. API Response: {data}")
                        return []

                    channel_info = data['items'][0]
                    uploads_playlist_id = channel_info['contentDetails']['relatedPlaylists']['uploads']
                    channel_name = channel_info['snippet']['title']

                # プレイリストアイテムを取得
                playlist_url = "https://www.googleapis.com/youtube/v3/playlistItems"
                playlist_params: Dict[str, str] = {
                    'part': 'snippet,contentDetails',
                    'playlistId': uploads_playlist_id,
                    'maxResults': '50',
                    'key': api_key
                }

                if next_page_token:
                    playlist_params['pageToken'] = next_page_token

                response = requests.get(playlist_url, params=playlist_params, timeout=30)
                data = response.json()

                if 'error' in data:
                    self.logger.error(f"API error: {data['error']['message']}")
                    break

                for item in data.get('items', []):
                    snippet = item['snippet']
                    video_id = snippet['resourceId']['videoId']

                    video = VideoMetadata(
                        video_id=video_id,
                        title=snippet.get('title', ''),
                        url=f"https://www.youtube.com/watch?v={video_id}",
                        published_at=snippet.get('publishedAt', ''),
                        description=snippet.get('description', '')[:500],
                        duration="",
                        views="",
                        channel_name=channel_name,
                        channel_id=self.channel_id
                    )
                    videos.append(video)

                next_page_token = data.get('nextPageToken')
                if not next_page_token:
                    break

                page_count += 1
                time.sleep(0.5)  # レート制限対策

        except Exception as e:
            self.logger.error(f"Failed to fetch videos via API: {e}")

        return videos

    def save_to_csv(self):
        """動画情報をCSVファイルに保存"""
        if not self.videos:
            self.logger.warning("No videos to save")
            return

        # CSVファイルに書き込み
        with open(self.output_file, 'w', encoding='utf-8', newline='') as f:
            fieldnames = [
                'video_id', 'title', 'url', 'published_at',
                'description', 'channel_name', 'channel_id'
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for video in self.videos:
                writer.writerow({
                    'video_id': video.video_id,
                    'title': video.title,
                    'url': video.url,
                    'published_at': video.published_at,
                    'description': video.description,
                    'channel_name': video.channel_name,
                    'channel_id': video.channel_id
                })

        self.logger.info(f"Saved {len(self.videos)} videos to {self.output_file}")

    def run(self):
        """メイン処理を実行"""
        self.logger.info(f"Fetching videos for channel: {self.channel_id}")

        # YouTube Data API経由で取得を試みる
        self.videos = self.fetch_all_videos_via_api()

        # APIが使えない場合はフィード経由で取得（最近の動画のみ）
        if not self.videos:
            self.logger.info("Fetching from RSS feed (recent videos only)...")
            feed_content = self.fetch_channel_feed()
            if feed_content:
                self.videos = self.parse_feed(feed_content)

        if self.videos:
            # 公開日時でソート（新しい順）
            self.videos.sort(key=lambda x: x.published_at, reverse=True)

            self.logger.info(f"Found {len(self.videos)} videos")

            # CSVに保存
            self.save_to_csv()

            # サマリー表示
            if self.verbose:
                print("\n=== Channel Summary ===")
                print(f"Channel ID: {self.channel_id}")
                if self.videos:
                    print(f"Channel Name: {self.videos[0].channel_name}")
                print(f"Total Videos: {len(self.videos)}")
                if self.videos:
                    # 最新と最古の動画
                    newest = self.videos[0]
                    oldest = self.videos[-1]
                    print(f"Newest Video: {newest.title[:50]}... ({newest.published_at[:10]})")
                    print(f"Oldest Video: {oldest.title[:50]}... ({oldest.published_at[:10]})")
                print(f"\nOutput saved to: {self.output_file}")
        else:
            self.logger.error("No videos found")


def main():
    """メインエントリーポイント"""
    parser = argparse.ArgumentParser(
        description='YouTube Channel Video Index Generator - チャンネル内の全動画情報をCSVに出力',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # チャンネルIDを指定して実行
  python channel_index.py UC_x5XG1OV2P6uZZ5FSM9Ttw

  # チャンネルURLから自動的にIDを取得
  python channel_index.py --from-url "https://www.youtube.com/@GoogleDevelopers"

  # 出力ファイル名を指定
  python channel_index.py UC_x5XG1OV2P6uZZ5FSM9Ttw -o my_channel.csv

  # YouTube Data APIを使用（環境変数 YOUTUBE_API_KEY が必要）
  export YOUTUBE_API_KEY="your-api-key"
  python channel_index.py UC_x5XG1OV2P6uZZ5FSM9Ttw --max-pages 20

注意:
  - RSS フィード経由では最新15-20件程度の動画のみ取得可能
  - 全動画を取得するには YouTube Data API キーが必要
        """
    )

    parser.add_argument('channel_input',
                       help='YouTube チャンネルID (UC...) またはチャンネルURL（--from-url使用時）')
    parser.add_argument('-o', '--output', default='index.csv',
                       help='出力CSVファイル名 (default: index.csv)')
    parser.add_argument('--from-url', action='store_true',
                       help='入力をチャンネルURLとして処理し、自動的にチャンネルIDを取得')
    parser.add_argument('--max-pages', type=int, default=10,
                       help='API使用時の最大ページ数 (1ページ50動画, default: 10)')
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='詳細な出力を表示')

    args = parser.parse_args()

    # チャンネルIDの取得
    channel_id = args.channel_input

    if args.from_url:
        # URLからチャンネルIDを取得
        generator = ChannelIndexGenerator("", args.output, args.max_pages, args.verbose)
        extracted_id = generator.get_channel_id_from_url(args.channel_input)
        if not extracted_id:
            print(f"Error: Could not extract channel ID from URL: {args.channel_input}")
            sys.exit(1)
        channel_id = extracted_id
        print(f"Extracted channel ID: {channel_id}")
    elif not channel_id.startswith('UC'):
        print(f"Warning: Channel ID should start with 'UC'. Got: {channel_id}")

    # インデックス生成を実行
    generator = ChannelIndexGenerator(
        channel_id=channel_id,
        output_file=args.output,
        max_pages=args.max_pages,
        verbose=args.verbose
    )
    generator.run()


if __name__ == '__main__':
    main()

