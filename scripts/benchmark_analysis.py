#!/usr/bin/env python3
"""Benchmark scene analysis without reusing the application cache."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remixer import detect_result, file_hash


def measure(source: Path, label: str, width: int | None, decode: str) -> dict:
    updates = []
    with tempfile.TemporaryDirectory(prefix='clip-remixer-benchmark-') as cache:
        started = time.perf_counter()
        result = detect_result(source, Path(cache), .15, width, decode, updates.append)
        analysis_seconds = time.perf_counter() - started
    return {
        'case': label,
        'requested_decode': decode,
        'actual_decode': result.actual_decode,
        'fallback_reason': result.fallback_reason,
        'analysis_seconds': round(analysis_seconds, 4),
        'shot_count': len(result.shots),
        'cut_timestamps': [round(shot.start, 6) for shot in result.shots[1:]],
        'progress_updates': len(updates),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path, help='Chemin local de la vidéo à analyser')
    args = parser.parse_args()
    source = args.source.expanduser().resolve(strict=True)
    fingerprint_started = time.perf_counter()
    file_hash(source)
    fingerprint_seconds = time.perf_counter() - fingerprint_started
    results = [
        measure(source, 'A-original-software', None, 'software'),
        measure(source, 'B-640-software', 640, 'software'),
        measure(source, 'C-640-videotoolbox', 640, 'videotoolbox'),
    ]
    baseline = results[0]['cut_timestamps']
    for result in results:
        result['cut_deltas_vs_A_seconds'] = [
            round(min((abs(point - reference) for reference in baseline), default=0), 6)
            for point in result['cut_timestamps']
        ]
    print(json.dumps({
        'source_name': source.name,
        'fingerprint_seconds': round(fingerprint_seconds, 4),
        'cache': 'isolated temporary cache per case',
        'results': results,
    }, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
