#!/usr/bin/env python3
"""
YouTube Video Transcript and Summary Tool

A tool to fetch YouTube video transcripts and generate AI summaries.
Supports batch processing with incremental updates.
"""

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from tqdm import tqdm
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, IpBlocked
import subprocess
import tempfile
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


@dataclass
class VideoInfo:
    """Video metadata container"""
    video_id: str
    title: str = ""
    url: str = ""
    published_at: str = ""
    language: str = ""
    transcript_chars: int = 0
    tokens_estimate: int = 0
    summary_status: str = "PENDING"
    error: str = ""


@dataclass
class SummaryResult:
    """Summary result container"""
    video_id: str
    title: str
    url: str
    published_at: str
    language: str
    summary: str
    highlights: List[str]
    new_insights: List[str]
    notable_quotes: List[Dict[str, str]]
    tokens_estimate: int


class YouTubeSummaryTool:
    """Main tool class for YouTube transcript and summary processing"""
    
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.outdir = Path(args.outdir)
        self.transcript_dir = self.outdir / "transcripts"
        self.summary_dir = self.outdir / "summaries"
        self.index_file = self.outdir / "index.csv"
        
        # Create directories
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        self.summary_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging
        self._setup_logging()
        
        # Load existing index
        self.index_data: Dict[str, VideoInfo] = self._load_index()
        
        # Rate limiting
        self.last_request_time = 0
        self.request_interval = 1.0 / args.rps
        
        # Setup proxy and cookies if provided
        self.proxies = None
        self.cookies = None
        self.cookies_file = None
        if args.proxy:
            self.proxies = {"https": args.proxy}
        if args.cookies_file and Path(args.cookies_file).exists():
            self.cookies_file = args.cookies_file
            with open(args.cookies_file, 'r') as f:
                self.cookies = f.read().strip()
        
    def _setup_logging(self):
        """Setup logging configuration"""
        log_format = '%(asctime)s - %(levelname)s - %(message)s'
        handlers = [logging.StreamHandler()]
        
        if self.args.log_file:
            handlers.append(logging.FileHandler(self.args.log_file))
            
        logging.basicConfig(
            level=logging.INFO,
            format=log_format,
            handlers=handlers
        )
        self.logger = logging.getLogger(__name__)
        
    def _load_index(self) -> Dict[str, VideoInfo]:
        """Load existing index from CSV"""
        index = {}
        if self.index_file.exists():
            with open(self.index_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    video_info = VideoInfo(
                        video_id=row['video_id'],
                        title=row.get('title', ''),
                        url=row.get('url', ''),
                        published_at=row.get('published_at', ''),
                        language=row.get('lang', ''),
                        transcript_chars=int(row.get('transcript_chars', 0)),
                        tokens_estimate=int(row.get('tokens_estimate', 0)),
                        summary_status=row.get('summary_status', 'PENDING'),
                        error=row.get('error', '')
                    )
                    index[row['video_id']] = video_info
        return index
        
    def _save_index(self):
        """Save index to CSV"""
        with open(self.index_file, 'w', encoding='utf-8', newline='') as f:
            fieldnames = ['video_id', 'title', 'url', 'published_at', 'lang', 
                         'transcript_chars', 'tokens_estimate', 'summary_status', 'error']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for video_info in self.index_data.values():
                writer.writerow({
                    'video_id': video_info.video_id,
                    'title': video_info.title,
                    'url': video_info.url,
                    'published_at': video_info.published_at,
                    'lang': video_info.language,
                    'transcript_chars': video_info.transcript_chars,
                    'tokens_estimate': video_info.tokens_estimate,
                    'summary_status': video_info.summary_status,
                    'error': video_info.error
                })
                
    def _rate_limit(self):
        """Apply rate limiting"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.request_interval:
            time.sleep(self.request_interval - elapsed)
        self.last_request_time = time.time()
        
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def _fetch_feed(self, url: str) -> str:
        """Fetch Atom feed with retry"""
        self._rate_limit()
        response = httpx.get(url, timeout=30)
        response.raise_for_status()
        return response.text
        
    def _parse_video_id(self, input_str: str) -> str:
        """Extract video ID from various input formats"""
        # Already a video ID
        if re.match(r'^[a-zA-Z0-9_-]{11}$', input_str):
            return input_str
            
        # YouTube URL
        if 'youtube.com' in input_str or 'youtu.be' in input_str:
            parsed = urlparse(input_str)
            if 'youtu.be' in parsed.netloc:
                return parsed.path.lstrip('/')
            else:
                query = parse_qs(parsed.query)
                if 'v' in query:
                    return query['v'][0]
                    
        return input_str
        
    def get_video_ids(self) -> List[Tuple[str, Dict[str, str]]]:
        """Get list of video IDs based on input arguments"""
        videos = []
        
        if self.args.channel_id:
            videos = self._get_channel_videos(self.args.channel_id)
        elif self.args.playlist_id:
            videos = self._get_playlist_videos(self.args.playlist_id)
        elif self.args.video_ids_file:
            videos = self._get_videos_from_file(self.args.video_ids_file)
        else:
            raise ValueError("No input source specified")
            
        # Apply max videos limit
        if self.args.max_videos:
            videos = videos[:self.args.max_videos]
            
        return videos
        
    def _get_channel_videos(self, channel_id: str) -> List[Tuple[str, Dict[str, str]]]:
        """Get videos from channel feed"""
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        feed_content = self._fetch_feed(url)
        return self._parse_feed(feed_content)
        
    def _get_playlist_videos(self, playlist_id: str) -> List[Tuple[str, Dict[str, str]]]:
        """Get videos from playlist feed"""
        url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
        feed_content = self._fetch_feed(url)
        return self._parse_feed(feed_content)
        
    def _parse_feed(self, feed_content: str) -> List[Tuple[str, Dict[str, str]]]:
        """Parse Atom feed and extract video information"""
        root = ET.fromstring(feed_content)
        ns = {'atom': 'http://www.w3.org/2005/Atom', 
              'media': 'http://search.yahoo.com/mrss/'}
        
        videos = []
        for entry in root.findall('atom:entry', ns):
            video_id_elem = entry.find('atom:id', ns)
            if video_id_elem is not None:
                video_id = video_id_elem.text.split(':')[-1]
                
                title_elem = entry.find('atom:title', ns)
                title = title_elem.text if title_elem is not None else ""
                
                published_elem = entry.find('atom:published', ns)
                published = published_elem.text if published_elem is not None else ""
                
                metadata = {
                    'title': title,
                    'published_at': published,
                    'url': f"https://www.youtube.com/watch?v={video_id}"
                }
                videos.append((video_id, metadata))
                
        return videos
        
    def _get_videos_from_file(self, file_path: str) -> List[Tuple[str, Dict[str, str]]]:
        """Get video IDs from file (text or CSV)"""
        videos = []
        file_path_obj = Path(file_path)
        
        # CSVファイルの場合
        if file_path_obj.suffix.lower() == '.csv':
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # video_id カラムから取得
                    video_id = row.get('video_id', '')
                    if not video_id:
                        # URLカラムから抽出を試みる
                        url = row.get('url', '')
                        if url:
                            video_id = self._parse_video_id(url)
                    
                    if video_id:
                        metadata = {
                            'title': row.get('title', ''),
                            'published_at': row.get('published_at', ''),
                            'url': row.get('url', f"https://www.youtube.com/watch?v={video_id}")
                        }
                        videos.append((video_id, metadata))
        else:
            # テキストファイルの場合（1行1動画ID/URL）
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        video_id = self._parse_video_id(line)
                        metadata = {
                            'title': '',
                            'published_at': '',
                            'url': f"https://www.youtube.com/watch?v={video_id}"
                        }
                        videos.append((video_id, metadata))
        return videos
        
    def fetch_transcript(self, video_id: str) -> Optional[Tuple[List[Dict], str, str]]:
        """Fetch transcript for a video"""
        try:
            # Check if already exists and not forcing
            json_path = self.transcript_dir / f"{video_id}.json"
            txt_path = self.transcript_dir / f"{video_id}.txt"
            
            if json_path.exists() and txt_path.exists() and not self.args.force:
                self.logger.info(f"Transcript already exists for {video_id}, skipping")
                with open(json_path, 'r', encoding='utf-8') as f:
                    segments = json.load(f)
                with open(txt_path, 'r', encoding='utf-8') as f:
                    text = f.read()
                # Detect language from existing data
                lang = self.index_data.get(video_id, VideoInfo(video_id=video_id)).language or 'unknown'
                return segments, text, lang
                
            # Try yt-dlp first if enabled
            if self.args.use_ytdlp:
                result = self._fetch_transcript_ytdlp(video_id)
                if result:
                    return result
                
            self._rate_limit()
            
            # Get available transcripts with proxy support
            kwargs = {}
            if self.proxies:
                kwargs['proxies'] = self.proxies
            # Note: youtube-transcript-api doesn't support cookies directly
                
            api = YouTubeTranscriptApi()
            transcript_list = api.list(video_id, **kwargs)
            
            # Try to get transcript in preferred languages
            languages = [lang.strip() for lang in self.args.languages.split(',')]
            transcript = None
            selected_lang = None
            
            # First try manually created transcripts
            for lang in languages:
                try:
                    transcript = transcript_list.find_manually_created_transcript([lang])
                    selected_lang = lang
                    break
                except:
                    continue
                    
            # Then try generated transcripts
            if not transcript:
                for lang in languages:
                    try:
                        transcript = transcript_list.find_generated_transcript([lang])
                        selected_lang = lang
                        break
                    except:
                        continue
                        
            # Try to get translated version
            if not transcript:
                try:
                    # Get any available transcript and translate to Japanese
                    for t in transcript_list:
                        transcript = t.translate('ja')
                        selected_lang = 'ja (translated)'
                        break
                except:
                    pass
                    
            if not transcript:
                raise ValueError("No suitable transcript found")
                
            # Fetch the transcript with proxy support
            fetch_kwargs = {}
            if self.proxies:
                fetch_kwargs['proxies'] = self.proxies
                
            segments = transcript.fetch(**fetch_kwargs)
            
            # Process segments
            processed_segments = []
            text_parts = []
            
            for segment in segments:
                # Clean text if requested
                # Handle both dict and object formats
                if isinstance(segment, dict):
                    text = segment['text']
                    start = segment['start']
                    duration = segment['duration']
                else:
                    # FetchedTranscriptSnippet object
                    text = segment.text
                    start = segment.start
                    duration = segment.duration
                    
                if self.args.clean_tags:
                    text = re.sub(r'\[.*?\]', '', text).strip()
                    
                processed_segment = {
                    'start': start,
                    'duration': duration,
                    'text': text
                }
                processed_segments.append(processed_segment)
                text_parts.append(text)
                
            full_text = ' '.join(text_parts)
            
            # Save to files
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(processed_segments, f, ensure_ascii=False, indent=2)
                
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(full_text)
                
            self.logger.info(f"Fetched transcript for {video_id} in {selected_lang}")
            return processed_segments, full_text, selected_lang
            
        except (TranscriptsDisabled, NoTranscriptFound) as e:
            self.logger.warning(f"No transcript available for {video_id}: {e}")
            # Try yt-dlp as fallback
            if not self.args.use_ytdlp:
                self.logger.info("Trying yt-dlp as fallback...")
                result = self._fetch_transcript_ytdlp(video_id)
                if result:
                    return result
            return None
        except IpBlocked as e:
            self.logger.error(f"IP blocked for {video_id}: {e}")
            self.logger.info("Trying yt-dlp as fallback...")
            # Try yt-dlp as fallback for IP block
            result = self._fetch_transcript_ytdlp(video_id)
            if result:
                return result
            self.logger.info("Consider using --proxy or --cookies-file options")
            return None
        except Exception as e:
            self.logger.error(f"Failed to fetch transcript for {video_id}: {e}")
            return None
            
    def _fetch_transcript_ytdlp(self, video_id: str) -> Optional[Tuple[List[Dict], str, str]]:
        """Fetch transcript using yt-dlp as alternative method"""
        try:
            self.logger.info(f"Fetching transcript for {video_id} using yt-dlp")
            
            # Prepare yt-dlp command
            url = f"https://www.youtube.com/watch?v={video_id}"
            
            # Get available subtitles info first
            info_cmd = [
                'yt-dlp',
                '--list-subs',
                '--no-warnings',
                '--user-agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                '--no-check-certificate',
                url
            ]
            
            if self.proxies and self.proxies.get('https'):
                info_cmd.extend(['--proxy', self.proxies['https']])
            if self.cookies_file:
                info_cmd.extend(['--cookies', self.cookies_file])
            
            # Check available subtitles
            result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=30)
            
            # Determine language to download
            languages = [lang.strip() for lang in self.args.languages.split(',')]
            selected_lang = None
            
            for lang in languages:
                if lang in result.stdout:
                    selected_lang = lang
                    break
                    
            if not selected_lang:
                # Try auto-generated
                if 'ja' in result.stdout or 'Japanese' in result.stdout:
                    selected_lang = 'ja'
                elif 'en' in result.stdout or 'English' in result.stdout:
                    selected_lang = 'en'
                else:
                    self.logger.warning(f"No suitable subtitles found for {video_id}")
                    return None
            
            # Download subtitles
            with tempfile.TemporaryDirectory() as tmpdir:
                output_path = Path(tmpdir) / '%(id)s.%(ext)s'
                
                cmd = [
                    'yt-dlp',
                    '--write-sub',
                    '--write-auto-sub',
                    '--sub-lang', selected_lang,
                    '--skip-download',
                    '--sub-format', 'vtt/ttml/srv1/srv2/srv3/json3/best',  # Try vtt first as it's more stable
                    '--no-warnings',
                    '--user-agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    '--no-check-certificate',
                    '--sleep-interval', '2',  # Sleep 2 seconds between requests
                    '--max-sleep-interval', '5',
                    '--extractor-args', 'youtube:player_client=android',  # Use Android client which is less restricted
                    '-o', str(output_path),
                    url
                ]
                
                if self.proxies and self.proxies.get('https'):
                    cmd.extend(['--proxy', self.proxies['https']])
                if self.cookies_file:
                    cmd.extend(['--cookies', self.cookies_file])
                
                # Execute yt-dlp
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                
                if result.returncode != 0:
                    self.logger.error(f"yt-dlp failed: {result.stderr}")
                    return None
                
                # Find the downloaded subtitle file
                subtitle_files = list(Path(tmpdir).glob(f"{video_id}*"))
                subtitle_files = [f for f in subtitle_files if f.suffix in ['.json3', '.srv3', '.srv2', '.srv1', '.vtt', '.ttml', '.srt']]
                if not subtitle_files:
                    self.logger.error(f"No subtitle file found for {video_id}")
                    return None
                
                # Parse subtitle file
                subtitle_file = subtitle_files[0]
                file_ext = subtitle_file.suffix.lower()
                self.logger.info(f"Found subtitle file: {subtitle_file.name}")
                
                segments = []
                text_parts = []
                
                if file_ext in ['.json3', '.srv3', '.srv2', '.srv1']:
                    # Parse JSON-based subtitle format
                    with open(subtitle_file, 'r', encoding='utf-8') as f:
                        subtitle_data = json.load(f)
                    
                    for event in subtitle_data.get('events', []):
                        if 'segs' in event:
                            text = ''.join(seg.get('utf8', '') for seg in event['segs'])
                            if text.strip():
                                # Clean text if requested
                                if self.args.clean_tags:
                                    text = re.sub(r'\[.*?\]', '', text).strip()
                                
                                segment = {
                                    'start': event.get('tStartMs', 0) / 1000.0,
                                    'duration': event.get('dDurationMs', 0) / 1000.0,
                                    'text': text
                                }
                                segments.append(segment)
                                text_parts.append(text)
                                
                elif file_ext == '.vtt':
                    # Parse VTT format
                    with open(subtitle_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    # Remove WEBVTT header and metadata
                    lines = content.split('\n')
                    
                    i = 0
                    while i < len(lines):
                        # Skip header and empty lines
                        if lines[i].startswith('WEBVTT') or lines[i].strip() == '' or lines[i].startswith('NOTE'):
                            i += 1
                            continue
                            
                        # Look for timestamp line
                        if ' --> ' in lines[i]:
                            timestamp_line = lines[i].strip()
                            
                            # Parse timestamp
                            match = re.match(r'(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})', timestamp_line)
                            if not match:
                                i += 1
                                continue
                            
                            start_time = match.group(1).replace(',', '.')
                            end_time = match.group(2).replace(',', '.')
                            
                            # Convert time to seconds
                            def time_to_seconds(time_str):
                                parts = time_str.split(':')
                                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                            
                            start = time_to_seconds(start_time)
                            end = time_to_seconds(end_time)
                            
                            # Collect text lines until next timestamp or empty line
                            text_lines = []
                            i += 1
                            while i < len(lines) and lines[i].strip() != '' and ' --> ' not in lines[i]:
                                text_lines.append(lines[i].strip())
                                i += 1
                            
                            # Join and clean text
                            text = ' '.join(text_lines)
                            text = re.sub(r'<[^>]+>', '', text)  # Remove HTML tags
                            text = re.sub(r'\{[^}]+\}', '', text)  # Remove style tags
                            if self.args.clean_tags:
                                text = re.sub(r'\[.*?\]', '', text).strip()
                            
                            text = text.strip()
                            if text:
                                segment = {
                                    'start': start,
                                    'duration': end - start,
                                    'text': text
                                }
                                segments.append(segment)
                                text_parts.append(text)
                        else:
                            i += 1
                
                if not segments:
                    self.logger.error(f"No valid segments found in subtitle for {video_id}")
                    return None
                
                full_text = ' '.join(text_parts)
                
                # Save to files
                json_path = self.transcript_dir / f"{video_id}.json"
                txt_path = self.transcript_dir / f"{video_id}.txt"
                
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(segments, f, ensure_ascii=False, indent=2)
                    
                with open(txt_path, 'w', encoding='utf-8') as f:
                    f.write(full_text)
                
                self.logger.info(f"Successfully fetched transcript for {video_id} using yt-dlp (lang: {selected_lang})")
                return segments, full_text, selected_lang
                
        except subprocess.TimeoutExpired:
            self.logger.error(f"yt-dlp timeout for {video_id}")
            return None
        except Exception as e:
            self.logger.error(f"Failed to fetch transcript with yt-dlp for {video_id}: {e}")
            return None
            
    def generate_summary(self, video_id: str, text: str, metadata: Dict[str, str]) -> Optional[SummaryResult]:
        """Generate AI summary for the transcript"""
        try:
            # Check if already exists and not forcing
            json_path = self.summary_dir / f"{video_id}.json"
            md_path = self.summary_dir / f"{video_id}.md"
            
            if json_path.exists() and md_path.exists() and not self.args.force:
                self.logger.info(f"Summary already exists for {video_id}, skipping")
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return SummaryResult(**data)
                    
            # Estimate tokens (rough estimate for Japanese: 2-3 chars per token)
            tokens_estimate = len(text) // 2
            
            # Apply map-reduce if text is too long
            if len(text) > self.args.chunk_chars * 2:
                summary_data = self._map_reduce_summary(video_id, text, metadata)
            else:
                summary_data = self._direct_summary(video_id, text, metadata)
                
            if summary_data:
                # Save JSON
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(asdict(summary_data), f, ensure_ascii=False, indent=2)
                    
                # Save Markdown
                md_content = self._format_markdown(summary_data)
                with open(md_path, 'w', encoding='utf-8') as f:
                    f.write(md_content)
                    
                self.logger.info(f"Generated summary for {video_id}")
                return summary_data
                
        except Exception as e:
            self.logger.error(f"Failed to generate summary for {video_id}: {e}")
            return None
            
    def _chunk_text(self, text: str) -> List[str]:
        """Split text into chunks with overlap"""
        chunks = []
        chunk_size = self.args.chunk_chars
        overlap = self.args.chunk_overlap
        
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunk = text[start:end]
            chunks.append(chunk)
            start += chunk_size - overlap
            
        return chunks
        
    def _map_reduce_summary(self, video_id: str, text: str, metadata: Dict[str, str]) -> Optional[SummaryResult]:
        """Map-reduce approach for long texts"""
        chunks = self._chunk_text(text)
        self.logger.info(f"Processing {len(chunks)} chunks for {video_id}")
        
        # Map phase: summarize each chunk
        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            prompt = f"""以下のテキストは、YouTubeの動画の文字起こしの一部（{i+1}/{len(chunks)}）です。
重要なポイントを箇条書きで3-5個抽出してください。

テキスト:
{chunk}

重要ポイント（箇条書き）:"""
            
            summary = self._call_ai(prompt, max_tokens=500)
            if summary:
                chunk_summaries.append(summary)
                
        if not chunk_summaries:
            return None
            
        # Reduce phase: combine chunk summaries
        combined_points = "\n".join(chunk_summaries)
        return self._generate_final_summary(video_id, combined_points, metadata, is_reduced=True)
        
    def _direct_summary(self, video_id: str, text: str, metadata: Dict[str, str]) -> Optional[SummaryResult]:
        """Direct summary for shorter texts"""
        return self._generate_final_summary(video_id, text, metadata, is_reduced=False)
        
    def _generate_final_summary(self, video_id: str, content: str, metadata: Dict[str, str], is_reduced: bool) -> Optional[SummaryResult]:
        """Generate final structured summary"""
        if is_reduced:
            context = "以下は動画の各セクションから抽出された重要ポイントのリストです。"
        else:
            context = "以下は動画の完全な文字起こしです。"
            
        prompt = f"""{context}
これを元に、以下の形式で日本語の要約を作成してください。

タイトル: {metadata.get('title', 'Unknown')}

{content}

以下のJSON形式で回答してください:
{{
  "summary": "1段落の要約（TL;DR）。会話の文脈や流れを踏まえた内容の本質を200文字程度で",
  "highlights": ["重要ポイント1", "重要ポイント2", ...],  // 最大10個
  "new_insights": ["会話から得られる新しい気づき1", ...],  // 3-5個、会話の相互作用から生まれる示唆
  "notable_quotes": [{{"t": "MM:SS", "text": "印象的な発言"}}, ...]  // 2-3個、時間は推定で可
}}"""
        
        response = self._call_ai(prompt, max_tokens=2000)
        if not response:
            return None
            
        try:
            # Parse JSON response
            data = json.loads(response)
            
            # Estimate tokens
            total_text = metadata.get('title', '') + content
            tokens_estimate = len(total_text) // 2
            
            return SummaryResult(
                video_id=video_id,
                title=metadata.get('title', ''),
                url=metadata.get('url', f"https://www.youtube.com/watch?v={video_id}"),
                published_at=metadata.get('published_at', ''),
                language=self.index_data.get(video_id, VideoInfo(video_id=video_id)).language,
                summary=data.get('summary', ''),
                highlights=data.get('highlights', [])[:10],
                new_insights=data.get('new_insights', [])[:5],
                notable_quotes=data.get('notable_quotes', [])[:3],
                tokens_estimate=tokens_estimate
            )
        except json.JSONDecodeError:
            self.logger.error(f"Failed to parse AI response as JSON for {video_id}")
            return None
            
    def _call_ai(self, prompt: str, max_tokens: int = 1000) -> Optional[str]:
        """Call AI provider (Anthropic or OpenAI)"""
        self._rate_limit()
        
        if self.args.provider == 'anthropic':
            return self._call_anthropic(prompt, max_tokens)
        elif self.args.provider == 'openai':
            return self._call_openai(prompt, max_tokens)
        else:
            raise ValueError(f"Unknown provider: {self.args.provider}")
            
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def _call_anthropic(self, prompt: str, max_tokens: int) -> Optional[str]:
        """Call Anthropic Claude API"""
        try:
            import anthropic
            
            api_key = os.getenv('ANTHROPIC_API_KEY')
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not found in environment or .env file")
                
            client = anthropic.Anthropic(api_key=api_key)
            
            response = client.messages.create(
                model=self.args.model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}]
            )
            
            return response.content[0].text
            
        except Exception as e:
            self.logger.error(f"Anthropic API call failed: {e}")
            return None
            
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def _call_openai(self, prompt: str, max_tokens: int) -> Optional[str]:
        """Call OpenAI API"""
        try:
            import openai
            
            api_key = os.getenv('OPENAI_API_KEY')
            if not api_key:
                raise ValueError("OPENAI_API_KEY not found in environment or .env file")
                
            client = openai.OpenAI(api_key=api_key)
            
            response = client.chat.completions.create(
                model=self.args.model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"} if "json" in prompt.lower() else None
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            self.logger.error(f"OpenAI API call failed: {e}")
            return None
            
    def _format_markdown(self, summary: SummaryResult) -> str:
        """Format summary as Markdown"""
        # Parse date if available
        date_str = ""
        if summary.published_at:
            try:
                dt = datetime.fromisoformat(summary.published_at.replace('Z', '+00:00'))
                date_str = dt.strftime('%Y-%m-%d')
            except:
                date_str = summary.published_at
                
        md = f"""# {summary.title}

- 公開日: {date_str}
- URL: {summary.url}
- 言語: {summary.language}

## TL;DR

{summary.summary}

## Highlights

"""
        for highlight in summary.highlights:
            md += f"- {highlight}\n"
            
        md += "\n## 新たな気づき\n\n"
        for insight in summary.new_insights:
            md += f"- {insight}\n"
            
        if summary.notable_quotes:
            md += "\n## 印象的な引用\n\n"
            for quote in summary.notable_quotes:
                md += f"- [{quote.get('t', '??:??')}] {quote.get('text', '')}\n"
                
        return md
        
    def run(self):
        """Main execution method"""
        if self.args.dry_run:
            self._dry_run()
            return
            
        # Get video IDs
        videos = self.get_video_ids()
        self.logger.info(f"Processing {len(videos)} videos")
        
        # Process each video
        for video_id, metadata in tqdm(videos, desc="Processing videos"):
            try:
                # Update or create video info
                if video_id not in self.index_data:
                    self.index_data[video_id] = VideoInfo(
                        video_id=video_id,
                        title=metadata.get('title', ''),
                        url=metadata.get('url', ''),
                        published_at=metadata.get('published_at', '')
                    )
                    
                video_info = self.index_data[video_id]
                
                # Update metadata if available
                if metadata.get('title'):
                    video_info.title = metadata['title']
                if metadata.get('published_at'):
                    video_info.published_at = metadata['published_at']
                if metadata.get('url'):
                    video_info.url = metadata['url']
                    
                # Fetch transcript
                transcript_result = self.fetch_transcript(video_id)
                if not transcript_result:
                    video_info.summary_status = 'TRANSCRIPT_UNAVAILABLE'
                    video_info.error = 'Failed to fetch transcript'
                    self._save_index()
                    continue
                    
                segments, text, language = transcript_result
                video_info.language = language
                video_info.transcript_chars = len(text)
                
                # Generate summary
                summary = self.generate_summary(video_id, text, metadata)
                if summary:
                    video_info.tokens_estimate = summary.tokens_estimate
                    video_info.summary_status = 'COMPLETED'
                    video_info.error = ''
                else:
                    video_info.summary_status = 'SUMMARY_FAILED'
                    video_info.error = 'Failed to generate summary'
                    
                # Save index after each video
                self._save_index()
                
            except Exception as e:
                self.logger.error(f"Error processing {video_id}: {e}")
                video_info = self.index_data.get(video_id, VideoInfo(video_id=video_id))
                video_info.summary_status = 'ERROR'
                video_info.error = str(e)
                self._save_index()
                
        self.logger.info("Processing completed")
        
    def _dry_run(self):
        """Dry run to show what would be processed"""
        videos = self.get_video_ids()
        print(f"\nDry run mode - would process {len(videos)} videos:")
        
        new_count = 0
        existing_count = 0
        
        for video_id, metadata in videos[:10]:  # Show first 10
            if video_id in self.index_data and not self.args.force:
                status = "EXISTS"
                existing_count += 1
            else:
                status = "NEW"
                new_count += 1
                
            print(f"  [{status}] {video_id}: {metadata.get('title', 'Unknown')[:50]}")
            
        if len(videos) > 10:
            print(f"  ... and {len(videos) - 10} more")
            
        # Count remaining
        for video_id, _ in videos[10:]:
            if video_id in self.index_data and not self.args.force:
                existing_count += 1
            else:
                new_count += 1
                
        print(f"\nSummary: {new_count} new, {existing_count} existing")
        if self.args.force:
            print("Force mode enabled - would regenerate all")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='YouTube Video Transcript and Summary Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # Input sources (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('--channel-id', help='YouTube channel ID')
    input_group.add_argument('--playlist-id', help='YouTube playlist ID')
    input_group.add_argument('--video-ids-file', help='File with video IDs/URLs (one per line)')
    
    # Output options
    parser.add_argument('--outdir', default=os.getenv('OUTPUT_DIR', './out'), 
                       help='Output directory (default: from .env or ./out)')
    parser.add_argument('--max-videos', type=int, 
                       default=int(os.getenv('MAX_VIDEOS', '50')), 
                       help='Maximum videos to process (default: from .env or 50)')
    
    # Transcript options
    parser.add_argument('--languages', default=os.getenv('LANGUAGES', 'ja,ja-JP,en'), 
                       help='Preferred languages in order (default: from .env or ja,ja-JP,en)')
    parser.add_argument('--clean-tags', action='store_true', 
                       default=os.getenv('CLEAN_TAGS', 'false').lower() == 'true',
                       help='Remove [tag] metadata from transcripts')
    
    # AI provider options
    parser.add_argument('--provider', choices=['anthropic', 'openai'], 
                       default=os.getenv('AI_PROVIDER', 'anthropic'), 
                       help='AI provider (default: from .env or anthropic)')
    parser.add_argument('--model', 
                       default=os.getenv('AI_MODEL', 'claude-3-5-sonnet-latest'),
                       help='Model name (default: from .env or claude-3-5-sonnet-latest)')
    parser.add_argument('--chunk-chars', type=int, 
                       default=int(os.getenv('CHUNK_SIZE', '6000')),
                       help='Characters per chunk for map-reduce (default: from .env or 6000)')
    parser.add_argument('--chunk-overlap', type=int, 
                       default=int(os.getenv('CHUNK_OVERLAP', '300')),
                       help='Overlap between chunks (default: from .env or 300)')
    parser.add_argument('--summary-max-tokens', type=int, default=2000,
                       help='Max tokens for summary (default: 2000)')
    
    # Processing options
    parser.add_argument('--force', action='store_true', 
                       help='Force regeneration of existing files')
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be processed without doing it')
    parser.add_argument('--rps', type=float, 
                       default=float(os.getenv('REQUESTS_PER_SECOND', '0.8')),
                       help='Requests per second limit (default: from .env or 0.8)')
    
    # Logging
    parser.add_argument('--log-file', help='Log file path')
    
    # Proxy and authentication
    parser.add_argument('--proxy', default=os.getenv('PROXY_URL'),
                       help='HTTP/HTTPS proxy URL (e.g., http://proxy.example.com:8080)')
    parser.add_argument('--cookies-file', default=os.getenv('COOKIES_FILE'),
                       help='Path to cookies.txt file for YouTube authentication')
    parser.add_argument('--use-ytdlp', action='store_true',
                       default=os.getenv('USE_YTDLP', 'false').lower() == 'true',
                       help='Use yt-dlp for transcript fetching (more robust, avoids IP blocks)')
    
    args = parser.parse_args()
    
    # Run the tool
    tool = YouTubeSummaryTool(args)
    tool.run()


if __name__ == '__main__':
    main()