import re
import requests
import sys

if len(sys.argv) < 2:
	print("Usage: python get_channel_id.py <YouTubeチャンネルURL>")
	sys.exit(1)

url = sys.argv[1]
html = requests.get(url, timeout=30).text
m = re.search(r'channelId":"(UC[0-9A-Za-z_-]+)"', html)
print(m.group(1) if m else "not found")
