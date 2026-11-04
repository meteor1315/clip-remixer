"""Local scene-based remixer. No network access or AI service."""
from __future__ import annotations
import hashlib
import json
import math
import random
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


class RemixError(ValueError):
    pass


def run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RemixError(f"Commande échouée ({args[0]}):\n{result.stderr[-6000:]}")
    return result.stdout


def tools() -> None:
    for name in ('ffmpeg', 'ffprobe'):
        if not shutil.which(name):
            raise RemixError(f'{name} introuvable. Installer FFmpeg : brew install ffmpeg')


def probe(path: Path) -> dict:
    tools()
    path = Path(path).expanduser().resolve(strict=True)
    data = json.loads(run(['ffprobe', '-v', 'error', '-show_streams',
                           '-show_format', '-of', 'json', str(path)]))
    video = next((s for s in data['streams'] if s['codec_type'] == 'video'
                  and not s.get('disposition', {}).get('attached_pic')), None)
    audio = next((s for s in data['streams'] if s['codec_type'] == 'audio'), None)
    if video:
        data['video_duration'] = float(video.get('duration') or data['format'].get('duration', 0))
    if audio:
        data['audio_duration'] = float(audio.get('duration') or data['format'].get('duration', 0))
    data['video'], data['audio'] = video, audio
    return data


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Shot:
    id: int
    start: float
    end: float


@dataclass(frozen=True)
class Clip:
    shot: int
    start: float
    frames: int


def detect(path: Path, cache: Path, threshold: float = .30) -> list[Shot]:
    """Decode locally with FFmpeg's scene score. Cache timestamps, not media."""
    if not .01 <= threshold <= 1:
        raise RemixError('Le seuil doit être compris entre 0.01 et 1.')
    info = probe(path)
    if not info['video'] or info.get('video_duration', 0) <= 0:
        raise RemixError('Vidéo absente ou durée inconnue.')
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f'{file_hash(path)}:{threshold}:scene-v1'.encode()).hexdigest()
    saved = cache / f'{key}.json'
    if saved.exists():
        try:
            return [Shot(**row) for row in json.loads(saved.read_text())]
        except (ValueError, TypeError):
            saved.unlink(missing_ok=True)
    # Log to disk to avoid buffering per-frame diagnostic output in RAM.
    with tempfile.TemporaryFile(mode='w+') as log:
        result = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-threads', '2',
            '-i', str(Path(path).resolve()), '-map', '0:V:0', '-an', '-sn', '-dn',
            '-vf', f"setpts=PTS-STARTPTS,select='gt(scene,{threshold})',showinfo",
            '-fps_mode', 'vfr', '-f', 'null', '-'], stdout=subprocess.DEVNULL, stderr=log)
        log.seek(0)
        points = [0.0]
        tail = ''
        for line in log:
            tail = (tail + line)[-6000:]
            match = re.search(r'\bpts_time:([\d.eE+\-]+)', line)
            if match:
                value = float(match.group(1))
                if points[-1] < value < info['video_duration']:
                    points.append(value)
        if result.returncode:
            raise RemixError(tail)
    points.append(info['video_duration'])
    shots = [Shot(i, a, b) for i, (a, b) in enumerate(zip(points, points[1:])) if b > a]
    temporary = saved.with_suffix('.tmp')
    temporary.write_text(json.dumps([asdict(s) for s in shots]))
    temporary.replace(saved)
    return shots


def plan(shots: list[Shot], duration: float = 20, fps: int = 30,
         minimum: float = 1, maximum: float = 3, count: int = 5, seed: int = 42,
         excluded: set[int] | None = None,
         ranges: list[tuple[float, float]] | None = None) -> list[list[Clip]]:
    """Seeded, bounded randomized search; one occurrence per shot per edit."""
    if not (1 <= fps <= 120 and 1 <= count <= 100 and 0 < minimum <= maximum
            and 0 < duration <= 120 and math.isfinite(duration)
            and math.isfinite(minimum) and math.isfinite(maximum)):
        raise RemixError('Paramètres invalides (durée <= 120 s, variantes <= 100).')
    target = round(duration * fps)
    low, high = math.ceil(minimum * fps), math.floor(maximum * fps)
    if low < 1 or high < low or target < low:
        raise RemixError('Durées incompatibles avec la cadence ou la durée finale.')
    excluded, ranges = excluded or set(), ranges or []
    for a, b in ranges:
        if not (math.isfinite(a) and math.isfinite(b) and 0 <= a < b):
            raise RemixError('Plage exclue invalide : utiliser début < fin, en secondes.')
    # Conservatively discard shots overlapping excluded ranges.
    candidates = []
    for shot in shots:
        if (shot.id in excluded or any(shot.start < b and shot.end > a for a, b in ranges)):
            continue
        first = math.ceil(shot.start * fps - 1e-8)
        last = math.floor(shot.end * fps + 1e-8)
        capacity = min(high, last - first)
        if capacity >= low:
            candidates.append((shot.id, first, last, capacity))
    rng = random.Random(seed)
    outputs, signatures = [], set()
    for _ in range(3000):
        if len(outputs) == count:
            return outputs
        pool = candidates.copy()
        rng.shuffle(pool)
        # Prefer non-neighboring source shots, but this is a soft constraint.
        ordered = []
        while pool:
            choices = [i for i, row in enumerate(pool)
                       if not ordered or abs(row[0] - ordered[-1][0]) > 1]
            ordered.append(pool.pop(rng.choice(choices) if choices else 0))
        capacities = []
        total = 0
        feasible = []
        for n, row in enumerate(ordered, 1):
            total += row[3]
            capacities.append(row[3])
            if n * low <= target <= total:
                feasible.append(n)
        if not feasible:
            continue
        n = rng.choice(feasible)
        chosen = ordered[:n]
        lengths, remaining = [], target
        for i, row in enumerate(chosen):
            rest = chosen[i + 1:]
            lower = max(low, remaining - sum(r[3] for r in rest))
            upper = min(row[3], remaining - len(rest) * low)
            length = rng.randint(lower, upper)
            lengths.append(length)
            remaining -= length
        clips = [Clip(row[0], rng.randint(row[1], row[2] - length) / fps, length)
                 for row, length in zip(chosen, lengths)]
        signature = tuple((c.shot, c.start, c.frames) for c in clips)
        if signature not in signatures:
            outputs.append(clips)
            signatures.add(signature)
    raise RemixError('Impossible de produire toutes les variantes distinctes. '
                     'Réduire leur nombre/la durée minimale, augmenter la durée maximale, '
                     'ou réintégrer des plans. Aucun plan ne sera répété dans un édit.')


def dimensions(video: dict, vertical: bool) -> tuple[int, int]:
    if vertical:
        return 1080, 1920
    width, height = int(video['width']), int(video['height'])
    sar = video.get('sample_aspect_ratio', '1:1').split(':')
    if len(sar) == 2 and float(sar[0]) > 0 and float(sar[1]) > 0:
        width = width * float(sar[0]) / float(sar[1])
    rotation = float(video.get('tags', {}).get('rotate', 0))
    for side in video.get('side_data_list', []):
        rotation = float(side.get('rotation', rotation))
    if round(rotation) % 180:
        width, height = height, width
    # Keep aspect ratio; cap to 1080p landscape / 1920p portrait, no upscaling.
    scale = min(1, 1920 / max(width, height), 1080 / min(width, height))
    return max(2, round(width * scale / 2) * 2), max(2, round(height * scale / 2) * 2)


def render(source: Path, clips: list[Clip], destination: Path, fps: int = 30,
           audio_mode: str = 'continuous', audio_start: float = 0,
           soundtrack: Path | None = None, vertical: bool = False,
           hardware: bool = False, progress: Callable[[float], None] | None = None) -> dict:
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise RemixError('Le fichier de sortie ne peut pas remplacer la source.')
    if not clips or not 1 <= fps <= 120 or audio_mode not in {'continuous', 'shuffle', 'external', 'mute'}:
        raise RemixError('Montage, cadence ou mode audio invalide.')
    if not math.isfinite(audio_start) or audio_start < 0:
        raise RemixError('Le début audio doit être positif ou nul.')
    info = probe(source)
    if not info['video']:
        raise RemixError('Aucun flux vidéo.')
    duration = sum(c.frames for c in clips) / fps
    for c in clips:
        if c.frames <= 0 or c.start < 0 or c.start + c.frames / fps > info['video_duration'] + 1e-6:
            raise RemixError('Un extrait dépasse la vidéo source.')
    audio_path = Path(soundtrack).expanduser().resolve() if audio_mode == 'external' and soundtrack else source
    if audio_mode == 'external' and soundtrack is None:
        raise RemixError('Sélectionner un fichier audio externe.')
    if audio_mode != 'mute':
        audio_info = probe(audio_path)
        if not audio_info['audio']:
            raise RemixError('Aucun flux audio : choisir le mode muet ou un autre fichier.')
        needed = max(c.start + c.frames / fps for c in clips) if audio_mode == 'shuffle' else audio_start + duration
        if audio_info.get('audio_duration', 0) + .03 < needed:
            raise RemixError('Bande-son trop courte pour le passage demandé.')
    width, height = dimensions(info['video'], vertical)
    geometry = (f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}'
                if vertical else f'scale={width}:{height}')
    filters = f'fps={fps},{geometry},setsar=1,format=yuv420p,setpts=PTS-STARTPTS'
    codec, fallback = 'libx264', False
    if hardware:
        encoders = run(['ffmpeg', '-hide_banner', '-encoders'])
        if 'h264_videotoolbox' in encoders:
            codec = 'h264_videotoolbox'
        else:
            fallback = True
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='clip-remixer-render-') as temp:
        root = Path(temp)
        segments = []
        for i, c in enumerate(clips):
            part = root / f'part-{i:04d}.mp4'
            base = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
                    '-threads', '2', '-ss', f'{c.start:.9f}', '-i', str(source),
                    '-map', '0:V:0', '-an', '-sn', '-dn', '-vf', filters,
                    '-frames:v', str(c.frames), '-fps_mode', 'cfr']
            def encode(name: str) -> None:
                options = ['-c:v', name, '-threads', '2']
                options += ['-preset', 'veryfast', '-crf', '20'] if name == 'libx264' else ['-b:v', '8M']
                run(base + options + ['-map_metadata', '-1', '-metadata:s:v:0', 'rotate=0', str(part)])
            try:
                encode(codec)
            except RemixError:
                if codec != 'h264_videotoolbox':
                    raise
                # Restart with CPU if hardware fails; re-encode prior segments too.
                return render(source, clips, destination, fps, audio_mode, audio_start,
                              soundtrack, vertical, False, progress) | {'hardware_fallback': True}
            segments.append(part)
            if progress:
                progress((i + 1) / (len(clips) + 1))
        concat = root / 'parts.txt'
        concat.write_text(''.join(f"file '{p.name}'\n" for p in segments))
        joined = root / 'joined.mp4'
        run(['ffmpeg', '-y', '-v', 'error', '-nostdin', '-f', 'concat', '-safe', '1',
             '-i', str(concat), '-map', '0:v:0', '-c', 'copy', str(joined)])
        command = ['ffmpeg', '-y', '-v', 'error', '-nostdin', '-i', str(joined)]
        if audio_mode == 'shuffle':
            parts = []
            for i, c in enumerate(clips):
                part = root / f'audio-{i:04d}.wav'
                run(['ffmpeg', '-y', '-v', 'error', '-nostdin', '-ss', str(c.start),
                     '-i', str(source), '-map', '0:a:0', '-vn', '-t', str(c.frames / fps),
                     '-af', 'asetpts=PTS-STARTPTS,aresample=48000,apad',
                     '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', str(part)])
                parts.append(part)
            listing = root / 'audio.txt'
            listing.write_text(''.join(f"file '{p.name}'\n" for p in parts))
            command += ['-f', 'concat', '-safe', '1', '-i', str(listing)]
        elif audio_mode in {'continuous', 'external'}:
            command += ['-ss', str(audio_start), '-i', str(audio_path)]
        command += ['-map', '0:v:0', '-c:v', 'copy']
        if audio_mode != 'mute':
            command += ['-map', '1:a:0', '-af', 'asetpts=PTS-STARTPTS',
                        '-c:a', 'aac', '-b:a', '192k']
        else:
            command += ['-an']
        staged = root / 'result.mp4'
        command += ['-t', str(duration), '-map_metadata', '-1', '-movflags', '+faststart', str(staged)]
        run(command)
        actual = probe(staged)
        if abs(actual['video_duration'] - duration) > 1 / fps + .001:
            raise RemixError('La durée exportée ne correspond pas au montage demandé.')
        shutil.copyfile(staged, destination)
    if progress:
        progress(1.0)
    return {'fps': fps, 'duration': duration, 'width': width, 'height': height,
            'codec': codec, 'hardware_fallback': fallback,
            'audio_mode': audio_mode, 'audio_start': audio_start,
            'clips': [asdict(c) for c in clips]}
