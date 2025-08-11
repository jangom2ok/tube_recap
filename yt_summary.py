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
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptList


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
        """Get video IDs from file"""
        videos = []
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
                
            self._rate_limit()
            
            # Get available transcripts
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            
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
                
            # Fetch the transcript
            segments = transcript.fetch()
            
            # Process segments
            processed_segments = []
            text_parts = []
            
            for segment in segments:
                # Clean text if requested
                text = segment['text']
                if self.args.clean_tags:
                    text = re.sub(r'\[.*?\]', '', text).strip()
                    
                processed_segment = {
                    'start': segment['start'],
                    'duration': segment['duration'],
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
            
        except Exception as e:
            self.logger.error(f"Failed to fetch transcript for {video_id}: {e}")
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
            
            api_key = os.environ.get('ANTHROPIC_API_KEY')
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY environment variable not set")
                
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
            
            api_key = os.environ.get('OPENAI_API_KEY')
            if not api_key:
                raise ValueError("OPENAI_API_KEY environment variable not set")
                
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
    parser.add_argument('--outdir', default='./out', help='Output directory (default: ./out)')
    parser.add_argument('--max-videos', type=int, default=50, help='Maximum videos to process (default: 50)')
    
    # Transcript options
    parser.add_argument('--languages', default='ja,ja-JP,en', 
                       help='Preferred languages in order (default: ja,ja-JP,en)')
    parser.add_argument('--clean-tags', action='store_true', 
                       help='Remove [tag] metadata from transcripts')
    
    # AI provider options
    parser.add_argument('--provider', choices=['anthropic', 'openai'], 
                       default='anthropic', help='AI provider (default: anthropic)')
    parser.add_argument('--model', default='claude-3-5-sonnet-latest',
                       help='Model name (default: claude-3-5-sonnet-latest)')
    parser.add_argument('--chunk-chars', type=int, default=6000,
                       help='Characters per chunk for map-reduce (default: 6000)')
    parser.add_argument('--chunk-overlap', type=int, default=300,
                       help='Overlap between chunks (default: 300)')
    parser.add_argument('--summary-max-tokens', type=int, default=2000,
                       help='Max tokens for summary (default: 2000)')
    
    # Processing options
    parser.add_argument('--force', action='store_true', 
                       help='Force regeneration of existing files')
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be processed without doing it')
    parser.add_argument('--rps', type=float, default=0.8,
                       help='Requests per second limit (default: 0.8)')
    
    # Logging
    parser.add_argument('--log-file', help='Log file path')
    
    args = parser.parse_args()
    
    # Run the tool
    tool = YouTubeSummaryTool(args)
    tool.run()


if __name__ == '__main__':
    main()