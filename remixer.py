"""Local scene-based remixer. No network access or AI service."""
from __future__ import annotations
import hashlib
import json
import math
import os
import selectors
import random
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event
from typing import Callable


class RemixError(ValueError):
    pass


class AnalysisCancelled(RemixError):
    pass


@dataclass(frozen=True)
class AnalysisProgress:
    stage: str
    fraction: float
    processed_seconds: float
    total_seconds: float
    elapsed_seconds: float
    eta_seconds: float | None = None
    decode_mode: str = 'Logiciel'
    fallback_reason: str | None = None


@dataclass(frozen=True)
class AnalysisResult:
    shots: list['Shot']
    cache_hit: bool
    requested_decode: str
    actual_decode: str
    fallback_reason: str | None = None


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


@dataclass(frozen=True)
class AudioPassage:
    start: float
    end: float
    duration: float


def prepare_video_zip(outputs: list[Path], archive_dir: Path,
                      batch_id: str) -> tuple[Path, int, int]:
    """Create or reuse a stored ZIP for exactly the supplied MP4 outputs."""
    if not outputs:
        raise RemixError('Le lot ne contient aucune vidéo à archiver.')
    archive_dir = Path(archive_dir).resolve()
    archive_dir.mkdir(parents=True, exist_ok=True)
    paths = [Path(output).resolve() for output in outputs]
    if len(set(paths)) != len(paths):
        raise RemixError('Le lot contient des vidéos dupliquées.')
    entries = []
    for path in paths:
        if path.parent != archive_dir or path.suffix.lower() != '.mp4' or not path.is_file():
            raise RemixError(f'Export MP4 manquant ou invalide : {path.name}')
        stat = path.stat()
        if stat.st_size <= 0:
            raise RemixError(f'Export MP4 incomplet ou vide : {path.name}')
        try:
            media = probe(path)
        except (OSError, RemixError, ValueError, json.JSONDecodeError) as error:
            raise RemixError(f'Export MP4 incomplet ou illisible : {path.name}') from error
        if not media.get('video') or media.get('video_duration', 0) <= 0:
            raise RemixError(f'Export MP4 incomplet ou sans vidéo : {path.name}')
        entries.append({'path': str(path), 'size': stat.st_size,
                        'mtime_ns': stat.st_mtime_ns})
    safe_batch_id = re.sub(r'[^A-Za-z0-9._-]+', '-', batch_id).strip('-._') or 'lot'
    archive = archive_dir / f'clip-remixer-{safe_batch_id}.zip'
    manifest = archive.with_suffix('.zip.manifest.json')
    expected = {'entries': entries,
                'names': [f'edit-{index:03d}.mp4' for index in range(1, len(paths) + 1)]}
    if archive.is_file() and manifest.is_file():
        try:
            if json.loads(manifest.read_text(encoding='utf-8')) == expected:
                return archive, len(paths), archive.stat().st_size
        except (OSError, ValueError, TypeError):
            pass
    temp_archive = None
    temp_manifest = None
    try:
        with tempfile.NamedTemporaryFile('wb', dir=archive_dir, prefix='.videos-',
                                         suffix='.zip.tmp', delete=False) as temporary:
            temp_archive = Path(temporary.name)
        with zipfile.ZipFile(temp_archive, 'w', compression=zipfile.ZIP_STORED) as bundle:
            for path, name in zip(paths, expected['names']):
                bundle.write(path, arcname=name)
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=archive_dir,
                                         prefix='.videos-', suffix='.manifest.tmp',
                                         delete=False) as temporary:
            temp_manifest = Path(temporary.name)
            json.dump(expected, temporary, ensure_ascii=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temp_archive, archive)
        os.replace(temp_manifest, manifest)
    except (OSError, zipfile.BadZipFile) as error:
        raise RemixError(f'Impossible de préparer l’archive du lot : {error}') from error
    finally:
        if temp_archive:
            temp_archive.unlink(missing_ok=True)
        if temp_manifest:
            temp_manifest.unlink(missing_ok=True)
    return archive, len(paths), archive.stat().st_size


def plan_audio_passages(durations: list[float], audio_duration: float,
                        start: float = 0, end: float | None = None,
                        mode: str = 'same', seed: int = 42,
                        avoid_overlap: bool = False,
                        minimum_gap: float = 5) -> list[AudioPassage]:
    """Plan complete, continuous audio passages before any export starts."""
    if mode not in {'same', 'successive', 'random'}:
        raise RemixError('Répartition audio invalide.')
    if not durations or not math.isfinite(audio_duration) or audio_duration <= 0:
        raise RemixError('Durée audio absente ou invalide.')
    if not math.isfinite(start) or start < 0:
        raise RemixError('Le début audio doit être positif ou nul.')
    usable_end = audio_duration if end is None else end
    if not math.isfinite(usable_end) or usable_end <= start:
        raise RemixError('La borne audio doit être supérieure au début.')
    if not math.isfinite(minimum_gap) or minimum_gap < 0:
        raise RemixError('L’écart minimal audio doit être positif ou nul.')
    if any(not math.isfinite(duration) or duration <= 0 for duration in durations):
        raise RemixError('Durée d’édit invalide pour la planification audio.')
    if usable_end > audio_duration + 1e-6:
        raise RemixError(f'La borne de fin ({usable_end:.3f} s) dépasse la durée audio '
                         f'disponible ({audio_duration:.3f} s).')
    if any(start + duration > usable_end + 1e-6 for duration in durations):
        longest = max(durations)
        raise RemixError(f'Plage audio insuffisante : {usable_end - start:.3f} s disponibles, '
                         f'{longest:.3f} s nécessaires pour un édit.')
    if mode == 'same':
        return [AudioPassage(start, start + duration, duration) for duration in durations]
    if mode == 'successive':
        passages = []
        cursor = start
        for duration in durations:
            passages.append(AudioPassage(cursor, cursor + duration, duration))
            cursor += duration
        if cursor > usable_end + 1e-6:
            raise RemixError(f'Passages successifs impossibles : {cursor - start:.3f} s '
                             f'nécessaires, {usable_end - start:.3f} s disponibles.')
        return passages
    gap = max(max(durations) if avoid_overlap else minimum_gap, 1e-6)
    latest = usable_end - max(durations)
    required = (len(durations) - 1) * gap
    if latest - start + 1e-6 < required:
        constraint = 'sans chevauchement' if avoid_overlap else f'avec un écart minimal de {minimum_gap:g} s'
        raise RemixError(f'Passages aléatoires impossibles {constraint} : '
                         f'{usable_end - start:.3f} s disponibles, '
                         f'{required + max(durations):.3f} s nécessaires.')
    slack = latest - start - required
    rng = random.Random(seed)
    offsets = sorted(rng.uniform(0, slack) for _ in durations)
    starts = [start + i * gap + offset for i, offset in enumerate(offsets)]
    return [AudioPassage(value, value + duration, duration)
            for value, duration in zip(starts, durations)]


def _emit_progress(callback: Callable[[AnalysisProgress], None] | None,
                   value: AnalysisProgress) -> None:
    if callback:
        callback(value)


def _analysis_dimensions(video: dict, width: int | None) -> tuple[int, int] | None:
    if width is None:
        return None
    if width < 2:
        raise RemixError('La largeur d’analyse doit être au moins de 2 pixels.')
    source_width, source_height = int(video['width']), int(video['height'])
    if source_width <= width:
        return source_width, source_height
    return width, max(2, round(source_height * width / source_width / 2) * 2)


def _analysis_cache_key(path: Path, threshold: float, analysis_width: int | None,
                        decode: str) -> str:
    algorithm = 'scene-v2-progressive'
    return hashlib.sha256(
        f'{file_hash(path)}:{threshold}:{analysis_width or "original"}:{decode}:{algorithm}'.encode()
    ).hexdigest()


def _read_analysis_process(process: subprocess.Popen[str], duration: float,
                           started: float, callback: Callable[[AnalysisProgress], None] | None,
                           cancel_event: Event | None,
                           decode_mode: str) -> tuple[list[float], str, bool]:
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, 'progress')
    selector.register(process.stderr, selectors.EVENT_READ, 'diagnostic')
    points = [0.0]
    processed = 0.0
    tail = ''
    hardware_seen = False
    stderr_done = stdout_done = False
    while not (stderr_done and stdout_done):
        if cancel_event and cancel_event.is_set():
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise AnalysisCancelled('Analyse annulée.')
        for key, _ in selector.select(timeout=.2):
            line = key.fileobj.readline()
            if line == '':
                selector.unregister(key.fileobj)
                if key.data == 'progress':
                    stdout_done = True
                else:
                    stderr_done = True
                continue
            if key.data == 'progress':
                match = re.match(r'out_time_ms=(\d+)', line)
                if match:
                    processed = max(processed, min(duration, int(match.group(1)) / 1_000_000))
                    elapsed = time.monotonic() - started
                    rate = processed / elapsed if processed > 0 else 0
                    eta = (duration - processed) / rate if rate > .05 and processed < duration else None
                    _emit_progress(callback, AnalysisProgress(
                        'analyse', processed / duration, processed, duration, elapsed, eta,
                        decode_mode))
            else:
                tail = (tail + line)[-6000:]
                hardware_seen = hardware_seen or bool(re.search(
                    r'videotoolbox_vld|hwaccel.*(?:initialized|used)|'
                    r'using.*videotoolbox|hardware accelerated', line, re.I))
                match = re.search(r'\bpts_time:([\d.eE+\-]+)', line)
                if match:
                    value = float(match.group(1))
                    if points[-1] < value < duration:
                        points.append(value)
    return points, tail, hardware_seen


def detect_result(path: Path, cache: Path, threshold: float = .30,
                  analysis_width: int | None = 640, decode: str = 'auto',
                  progress: Callable[[AnalysisProgress], None] | None = None,
                  cancel_event: Event | None = None) -> AnalysisResult:
    """Analyse scene changes from the source, optionally after CPU scaling."""
    if not .01 <= threshold <= 1:
        raise RemixError('Le seuil doit être compris entre 0.01 et 1.')
    if decode not in {'auto', 'software', 'videotoolbox'}:
        raise RemixError('Mode de décodage invalide.')
    info = probe(path)
    if not info['video'] or info.get('video_duration', 0) <= 0:
        raise RemixError('Vidéo absente ou durée inconnue.')
    cache.mkdir(parents=True, exist_ok=True)
    requested = {'auto': 'Auto', 'software': 'Logiciel', 'videotoolbox': 'VideoToolbox'}[decode]
    last_fraction = 0.0

    def report(value: AnalysisProgress) -> None:
        nonlocal last_fraction
        fraction = max(last_fraction, min(1., value.fraction))
        last_fraction = fraction
        _emit_progress(progress, AnalysisProgress(
            value.stage, fraction, max(value.processed_seconds, fraction * info['video_duration']),
            value.total_seconds, value.elapsed_seconds, value.eta_seconds,
            value.decode_mode, value.fallback_reason))

    key = _analysis_cache_key(path, threshold, analysis_width, decode)
    saved = cache / f'{key}.json'
    if saved.exists():
        try:
            cached = json.loads(saved.read_text())
            if isinstance(cached, dict):
                rows = cached['shots']
                cached_actual = cached.get('actual_decode', requested)
                cached_fallback = cached.get('fallback_reason')
            else:
                rows, cached_actual, cached_fallback = cached, requested, None
            shots = [Shot(**row) for row in rows]
            report(AnalysisProgress(
                'résultat en cache', 1, info['video_duration'], info['video_duration'], 0,
                0, cached_actual, cached_fallback))
            return AnalysisResult(shots, True, requested, cached_actual, cached_fallback)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            saved.unlink(missing_ok=True)
    report(AnalysisProgress(
        'lecture/empreinte', 0, 0, info['video_duration'], 0, None, requested))
    dimensions_for_analysis = _analysis_dimensions(info['video'], analysis_width)
    video_filter = '[0:V:0]setpts=PTS-STARTPTS,split=2[scene][clock];'
    if dimensions_for_analysis:
        width, height = dimensions_for_analysis
        video_filter += (f'[scene]scale={width}:{height},'
                         f'select=\'gt(scene,{threshold})+eq(n,0)\',showinfo[detected];')
    else:
        video_filter += f'[scene]select=\'gt(scene,{threshold})+eq(n,0)\',showinfo[detected];'
    video_filter += '[clock]null[clockout]'
    base = ['ffmpeg', '-hide_banner', '-loglevel', 'verbose', '-nostdin', '-threads', '2',
            '-progress', 'pipe:1',
            '-stats_period', '250ms']
    if decode in {'auto', 'videotoolbox'}:
        base += ['-hwaccel', 'videotoolbox']
    base += ['-i', str(Path(path).resolve()), '-filter_complex', video_filter,
             '-map', '[detected]', '-an', '-sn', '-dn', '-fps_mode', 'vfr', '-f', 'null', '-',
             '-map', '[clockout]', '-an', '-sn', '-dn', '-f', 'null', '-']
    actual = 'VideoToolbox' if decode in {'auto', 'videotoolbox'} else 'Logiciel'
    fallback_reason = None
    started = time.monotonic()
    try:
        process = subprocess.Popen(base, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, bufsize=1)
        points, tail, hardware_seen = _read_analysis_process(
            process, info['video_duration'], started, report, cancel_event, actual)
        return_code = process.wait()
        if return_code:
            raise RemixError(tail or 'Échec du décodage FFmpeg.')
        if decode in {'auto', 'videotoolbox'} and not hardware_seen:
            actual = 'Logiciel'
            fallback_reason = 'FFmpeg n’a pas confirmé l’utilisation de VideoToolbox.'
    except (OSError, RemixError) as error:
        if decode not in {'auto', 'videotoolbox'} or isinstance(error, AnalysisCancelled):
            raise
        fallback_reason = str(error)
        report(AnalysisProgress(
            'analyse', 0, 0, info['video_duration'], time.monotonic() - started,
            None, 'Logiciel', fallback_reason))
        process = subprocess.Popen(
            ['ffmpeg', '-hide_banner', '-loglevel', 'verbose', '-nostdin', '-threads', '2',
             '-progress', 'pipe:1',
             '-stats_period', '250ms', '-i', str(Path(path).resolve()),
             '-filter_complex', video_filter, '-map', '[detected]', '-an', '-sn', '-dn',
             '-fps_mode', 'vfr', '-f', 'null', '-', '-map', '[clockout]', '-an', '-sn',
             '-dn', '-f', 'null', '-'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1)
        points, tail, _ = _read_analysis_process(
            process, info['video_duration'], time.monotonic(), report, cancel_event, 'Logiciel')
        if process.wait():
            raise RemixError(tail or 'Échec du décodage logiciel FFmpeg.')
        actual = 'Logiciel'
    points.append(info['video_duration'])
    shots = [Shot(i, a, b) for i, (a, b) in enumerate(zip(points, points[1:])) if b > a]
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=cache,
                                         prefix=f'.{key}.', suffix='.tmp', delete=False) as temporary:
            temporary_name = temporary.name
            json.dump({'shots': [asdict(s) for s in shots],
                       'actual_decode': actual, 'fallback_reason': fallback_reason}, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, saved)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
    report(AnalysisProgress(
        'finalisation', 1, info['video_duration'], info['video_duration'],
        time.monotonic() - started, 0, actual, fallback_reason))
    return AnalysisResult(shots, False, requested, actual, fallback_reason)


def detect(path: Path, cache: Path, threshold: float = .30,
           analysis_width: int | None = 640, decode: str = 'auto',
           progress: Callable[[AnalysisProgress], None] | None = None,
           cancel_event: Event | None = None) -> list[Shot]:
    return detect_result(path, cache, threshold, analysis_width, decode, progress, cancel_event).shots


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
