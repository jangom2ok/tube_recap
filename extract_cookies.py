#!/usr/bin/env python3
"""
Extract YouTube cookies from browser for authentication

This script extracts cookies from your browser and saves them in a format
that can be used with yt-dlp and youtube-transcript-api.
"""

import argparse
import os
import sqlite3
import tempfile
from pathlib import Path
import shutil
import sys
from typing import Optional

# Try to import browser_cookie3 for cross-platform cookie extraction
try:
    import browser_cookie3  # type: ignore[import-not-found]
    HAS_BROWSER_COOKIE3 = True
except ImportError:
    browser_cookie3 = None  # type: ignore[assignment]
    HAS_BROWSER_COOKIE3 = False


def get_chrome_cookies_db() -> Optional[Path]:
    """Get Chrome cookies database path"""
    if sys.platform == 'darwin':  # macOS
        base = Path.home() / "Library/Application Support/Google/Chrome"
    elif sys.platform.startswith('linux'):
        base = Path.home() / ".config/google-chrome"
    elif sys.platform == 'win32':
        base = Path(os.environ['LOCALAPPDATA']) / "Google/Chrome/User Data"
    else:
        return None

    # Try different profile locations
    for profile in ["Default", "Profile 1", "Profile 2"]:
        cookies_path = base / profile / "Cookies"
        if cookies_path.exists():
            return cookies_path

    return None


def get_safari_cookies_db() -> Optional[Path]:
    """Get Safari cookies database path (macOS only)"""
    if sys.platform != 'darwin':
        return None

    cookies_path = Path.home() / "Library/Cookies/Cookies.binarycookies"
    if cookies_path.exists():
        return cookies_path

    return None


def extract_cookies_sqlite(db_path: Path, domain: str = '.youtube.com') -> list[str]:
    """Extract cookies from SQLite database (Chrome/Firefox style)"""
    # Create a temporary copy to avoid locking issues
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name

    try:
        shutil.copy2(db_path, tmp_path)

        conn = sqlite3.connect(tmp_path)
        cursor = conn.cursor()

        # Try to get cookies
        try:
            cursor.execute("""
                SELECT host_key, name, value, path, expires_utc, is_secure, is_httponly, has_expires
                FROM cookies
                WHERE host_key LIKE ?
            """, (f'%{domain}%',))
        except sqlite3.OperationalError:
            # Try alternative column names
            cursor.execute("""
                SELECT host, name, value, path, expiry, isSecure, isHttpOnly, 1
                FROM moz_cookies
                WHERE host LIKE ?
            """, (f'%{domain}%',))

        cookies = []
        for row in cursor.fetchall():
            host, name, value, path, expires, secure, _, has_expires = row

            # Netscape cookie format
            cookie_line = '\t'.join([
                host,
                'TRUE' if host.startswith('.') else 'FALSE',
                path,
                'TRUE' if secure else 'FALSE',
                str(expires) if has_expires else '0',
                name,
                value
            ])
            cookies.append(cookie_line)

        conn.close()
        return cookies

    finally:
        # Clean up temp file
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def extract_cookies_browser_cookie3(browser: str = 'chrome', domain: str = 'youtube.com') -> Optional[list[str]]:
    """Extract cookies using browser_cookie3 library"""
    if not HAS_BROWSER_COOKIE3:
        return None

    try:
        if browser.lower() == 'chrome':
            cj = browser_cookie3.chrome(domain_name=domain)  # type: ignore[attr-defined]
        elif browser.lower() == 'firefox':
            cj = browser_cookie3.firefox(domain_name=domain)  # type: ignore[attr-defined]
        elif browser.lower() == 'safari':
            cj = browser_cookie3.safari(domain_name=domain)  # type: ignore[attr-defined]
        elif browser.lower() == 'edge':
            cj = browser_cookie3.edge(domain_name=domain)  # type: ignore[attr-defined]
        else:
            return None

        cookies: list[str] = []
        for cookie in cj:
            # Netscape cookie format
            cookie_line = '\t'.join([
                str(cookie.domain),
                'TRUE' if str(cookie.domain).startswith('.') else 'FALSE',
                str(cookie.path),
                'TRUE' if cookie.secure else 'FALSE',
                str(int(cookie.expires)) if cookie.expires else '0',
                str(cookie.name),
                str(cookie.value)
            ])
            cookies.append(cookie_line)

        return cookies

    except Exception as e:
        print(f"Error extracting cookies with browser_cookie3: {e}", file=sys.stderr)
        return None


def save_cookies_netscape(cookies: list[str], output_file: str) -> None:
    """Save cookies in Netscape format (compatible with yt-dlp)"""
    with open(output_file, 'w') as f:
        # Netscape cookies file header
        f.write("# Netscape HTTP Cookie File\n")
        f.write("# This file was generated by extract_cookies.py\n")
        f.write("# https://curl.haxx.se/rfc/cookie_spec.html\n\n")

        for cookie in cookies:
            f.write(cookie + '\n')


def main() -> int:
    parser = argparse.ArgumentParser(description='Extract YouTube cookies from browser')
    parser.add_argument('--browser', choices=['chrome', 'firefox', 'safari', 'edge', 'auto'],
                       default='auto', help='Browser to extract cookies from')
    parser.add_argument('--output', default='cookies.txt',
                       help='Output file for cookies (default: cookies.txt)')
    parser.add_argument('--method', choices=['auto', 'sqlite', 'browser_cookie3'],
                       default='auto', help='Method to use for extraction')

    args = parser.parse_args()

    cookies = None

    # Try browser_cookie3 first if available
    if args.method in ['auto', 'browser_cookie3'] and HAS_BROWSER_COOKIE3:
        if args.browser == 'auto':
            # Try different browsers
            for browser in ['chrome', 'firefox', 'safari', 'edge']:
                print(f"Trying {browser}...", file=sys.stderr)
                cookies = extract_cookies_browser_cookie3(browser)
                if cookies:
                    print(f"Successfully extracted cookies from {browser}", file=sys.stderr)
                    break
        else:
            cookies = extract_cookies_browser_cookie3(args.browser)

    # Fallback to direct SQLite extraction
    if not cookies and args.method in ['auto', 'sqlite']:
        if args.browser in ['chrome', 'auto']:
            db_path = get_chrome_cookies_db()
            if db_path:
                print(f"Trying Chrome cookies database: {db_path}", file=sys.stderr)
                try:
                    cookies = extract_cookies_sqlite(db_path)
                    if cookies:
                        print("Successfully extracted cookies from Chrome", file=sys.stderr)
                except Exception as e:
                    print(f"Error extracting Chrome cookies: {e}", file=sys.stderr)

    if not cookies:
        print("Failed to extract cookies. Try installing browser_cookie3:", file=sys.stderr)
        print("  pip install browser_cookie3", file=sys.stderr)
        print("\nOr manually export cookies using a browser extension.", file=sys.stderr)
        return 1

    # Save cookies
    save_cookies_netscape(cookies, args.output)
    print(f"Cookies saved to {args.output}")
    print(f"Found {len(cookies)} cookies for YouTube")

    # Check for important cookies
    important_cookies = ['LOGIN_INFO', 'CONSENT', 'PREF', 'SID', 'HSID', 'SSID']
    found_important = [line.split('\t')[-2] for line in cookies if len(line.split('\t')) > 6]
    found_important = [c for c in found_important if c in important_cookies]

    if found_important:
        print(f"Found important cookies: {', '.join(found_important)}")
    else:
        print("Warning: Some important cookies might be missing. Make sure you're logged into YouTube.")

    return 0


if __name__ == '__main__':
    sys.exit(main())
