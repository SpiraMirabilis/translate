#!/usr/bin/env python3
"""
Print a summary of recent reader activity from the reader_log table.

Usage:
    python reader_stats.py                 # last 24 hours, grouped by IP (default)
    python reader_stats.py 7d              # last 7 days
    python reader_stats.py 12h book        # group by book instead of IP
    python reader_stats.py 30m             # last 30 minutes
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from db_backend import create_backend
from reader_stats_core import collect_reader_stats, parse_duration


def _print_ip_view(data):
    for entry in data["ips"]:
        print(f"  {entry['label']}")
        for book in entry["books"]:
            parts = []
            if book["chapter_views"]:
                parts.append(f"ch {book['chapter_ranges']} ({book['chapter_views']} views)")
            if book["epub_downloads"]:
                parts.append(f"EPUB download x{book['epub_downloads']}")
            print(f"    {book['title']}  {', '.join(parts)}")
        print()


def _print_book_view(data):
    for entry in data["books"]:
        bits = []
        if entry["chapter_views"]:
            bits.append(f"{entry['chapter_views']} chapter views")
        if entry["epub_downloads"]:
            bits.append(f"{entry['epub_downloads']} EPUB downloads")
        bits.append(f"{entry['unique_ips']} unique IPs")
        print(f"  {entry['title']}  ({', '.join(bits)})")
        if entry["chapter_ranges"]:
            print(f"    chapters: {entry['chapter_ranges']}")
        for reader in entry["readers"]:
            print(f"    {reader['label']}  ({reader['view_count']} views)")
        print()


def main():
    args = sys.argv[1:]
    duration_str = args[0] if args else "24h"
    group_by = args[1] if len(args) > 1 else "ip"
    if group_by not in ("ip", "book"):
        print(f"Invalid group_by: {group_by!r} (use 'ip' or 'book')")
        sys.exit(1)
    try:
        parse_duration(duration_str)
    except ValueError as e:
        print(e)
        sys.exit(1)

    data = collect_reader_stats(create_backend(), duration_str, group_by=group_by)

    if data["total_views"] == 0:
        print(f"No reader activity in the last {duration_str}.")
        return

    print(f"Reader activity — last {duration_str} "
          f"({data['total_views']} total views, {data['unique_ips']} unique IPs) "
          f"[grouped by {group_by}]\n")

    if group_by == "ip":
        _print_ip_view(data)
    else:
        _print_book_view(data)


if __name__ == "__main__":
    main()
