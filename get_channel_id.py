#!/usr/bin/env python3
import subprocess
import sys
import re
import requests

def get_channel_id_with_ytdlp(url):
    """yt-dlpを使ってチャンネルIDを取得"""
    try:
        # --no-playlistオプションを追加して単一の結果のみ取得
        result = subprocess.run(
            ['yt-dlp', '--no-playlist', '--print', 'channel_id', url],
            capture_output=True,
            text=True,
            timeout=15
        )
        
        if result.returncode == 0 and result.stdout.strip():
            # 最初の行のみ取得（重複を避ける）
            channel_id = result.stdout.strip().split('\n')[0]
            if channel_id.startswith('UC'):
                return channel_id
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None

def get_channel_id_with_requests(url):
    """requestsを使ってHTMLからチャンネルIDを取得"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        html = response.text
        
        # 様々なパターンでチャンネルIDを検索
        patterns = [
            r'"channelId":"(UC[0-9A-Za-z_-]{22})"',
            r'"browseId":"(UC[0-9A-Za-z_-]{22})"',
            r'channel/(UC[0-9A-Za-z_-]{22})',
            r'"externalChannelId":"(UC[0-9A-Za-z_-]{22})"',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, html)
            if matches:
                # UCで始まる最初のマッチを返す
                return matches[0]
    except:
        pass
    return None

def main():
    if len(sys.argv) < 2:
        print("Usage: python get_channel_id.py <YouTubeチャンネルURL>")
        sys.exit(1)
    
    url = sys.argv[1]
    
    # まずyt-dlpを試す
    channel_id = get_channel_id_with_ytdlp(url)
    
    # yt-dlpが失敗したらrequestsを試す
    if not channel_id:
        channel_id = get_channel_id_with_requests(url)
    
    if channel_id:
        print(channel_id)
    else:
        print("not found")
        sys.exit(1)

if __name__ == "__main__":
    main()