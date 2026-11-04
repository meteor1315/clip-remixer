import json
import math
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remixer import (AnalysisCancelled, AnalysisProgress, Clip, RemixError, Shot, detect,
                     detect_result, dimensions, plan, plan_audio_passages, prepare_video_zip,
                     probe, render)


def test_planning():
    shots = [Shot(i, i*4., (i+1)*4.) for i in range(30)]
    results = plan(shots, 20, 30, 1, 3, 10, 123)
    assert results == plan(shots, 20, 30, 1, 3, 10, 123)
    assert results != plan(shots, 20, 30, 1, 3, 10, 124)
    assert len({tuple(v) for v in results}) == 10
    for variant in results:
        assert sum(c.frames for c in variant) == 600
        assert len({c.shot for c in variant}) == len(variant)
        for c in variant:
            assert 30 <= c.frames <= 90
            assert c.start >= shots[c.shot].start
            assert c.start + c.frames/30 <= shots[c.shot].end + 1e-8


def test_exclusions():
    shots = [Shot(i, i*4., (i+1)*4.) for i in range(20)]
    result = plan(shots, 15, 25, 1, 3, 2, 1, {0, 4}, [(8, 12)])
    assert all(c.shot not in {0, 2, 4} for variant in result for c in variant)


def test_fractional_boundaries():
    shots = [Shot(i, i*2.33+.01, (i+1)*2.33) for i in range(20)]
    for variant in plan(shots, 15, 24, .5, 2, 5, 0):
        for c in variant:
            assert c.start >= shots[c.shot].start - 1e-8
            assert c.start+c.frames/24 <= shots[c.shot].end+1e-8


def test_audio_same_passage_and_successive_nonzero_start():
    same = plan_audio_passages([20, 20], 100, 30, mode='same')
    assert [(p.start, p.end) for p in same] == [(30, 50), (30, 50)]
    successive = plan_audio_passages([20, 20, 20], 100, 30, mode='successive')
    assert [(p.start, p.end) for p in successive] == [(30, 50), (50, 70), (70, 90)]


def test_audio_random_is_reproducible_and_independent():
    first = plan_audio_passages([10] * 4, 100, 5, mode='random', seed=7,
                                minimum_gap=12)
    second = plan_audio_passages([10] * 4, 100, 5, mode='random', seed=7,
                                 minimum_gap=12)
    other_seed = plan_audio_passages([10] * 4, 100, 5, mode='random', seed=8,
                                     minimum_gap=12)
    assert first == second
    assert first != other_seed
    assert all(b.start - a.start >= 12 for a, b in zip(first, first[1:]))


def test_audio_no_overlap_exact_fill_and_impossible():
    passages = plan_audio_passages([10, 10, 10], 30, 0, mode='random',
                                   seed=1, avoid_overlap=True)
    assert [(p.start, p.end) for p in passages] == [(0, 10), (10, 20), (20, 30)]
    with pytest.raises(RemixError, match='impossibles'):
        plan_audio_passages([10, 10, 10], 29, 0, mode='random',
                            avoid_overlap=True)
    with pytest.raises(RemixError, match='insuffisante'):
        plan_audio_passages([20], 30, 15, end=29)


def test_audio_range_applies_to_whole_passage():
    with pytest.raises(RemixError, match='insuffisante'):
        plan_audio_passages([20], 100, 30, end=45, mode='same')


@pytest.mark.parametrize('kwargs', [dict(duration=0), dict(minimum=4, maximum=2),
    dict(fps=0), dict(count=101), dict(ranges=[(4, 2)]), dict(duration=float('nan'))])
def test_invalid(kwargs):
    with pytest.raises(RemixError):
        plan([Shot(0, 0, 40)], **kwargs)


def test_impossible():
    with pytest.raises(RemixError):
        plan([Shot(0, 0, 2)], 20)
    with pytest.raises(RemixError):
        plan([Shot(0, 0, 1)], 1, 30, 1, 1, 2)


def test_dimensions():
    assert dimensions({'width':1920, 'height':1080}, False) == (1920,1080)
    assert dimensions({'width':1920, 'height':1080,
                       'side_data_list':[{'rotation':90}]}, False) == (1080,1920)
    assert dimensions({'width':640, 'height':480, 'sample_aspect_ratio':'4:3'}, False) == (854,480)
    assert dimensions({'width':640, 'height':480}, True) == (1080,1920)


@pytest.fixture(scope='module')
def media(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg absent')
    root = tmp_path_factory.mktemp('media')
    path = root/'source.mp4'
    command = ['ffmpeg','-y','-v','error','-threads','1']
    for color in ['red', 'lime', 'blue', 'white']:
        command += ['-f','lavfi','-i',f'color=c={color}:s=160x90:r=24:d=1']
    command += ['-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=4',
        '-filter_complex_threads','1','-filter_complex',
        '[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]',
        '-map','[v]','-map','4:a','-c:v','libx264','-threads','1',
        '-pix_fmt','yuv420p','-c:a','aac',str(path)]
    subprocess.run(command, check=True, capture_output=True)
    return path


def test_detection_and_cache(media, tmp_path):
    shots = detect(media, tmp_path/'cache', .15, analysis_width=None, decode='software')
    assert len(shots) == 4
    for i, s in enumerate(shots):
        assert abs(s.start-i) < 1/24
        assert abs(s.end-(i+1)) < 1/24
    assert shots == detect(media, tmp_path/'cache', .15, analysis_width=None, decode='software')


def test_detection_reduced_resolution_and_cache_key(media, tmp_path):
    original = detect(media, tmp_path/'cache', .15, analysis_width=None, decode='software')
    reduced_result = detect_result(media, tmp_path/'cache', .15, analysis_width=64,
                                   decode='software')
    assert len(reduced_result.shots) == len(original)
    assert all(abs(a.start - b.start) <= 1 / 24 for a, b in
               zip(original, reduced_result.shots))
    assert len(list((tmp_path/'cache').glob('*.json'))) == 2


def test_progress_is_monotone_and_finalized(media, tmp_path):
    updates = []
    result = detect_result(media, tmp_path/'cache', .15, analysis_width=64,
                           decode='software', progress=updates.append)
    fractions = [item.fraction for item in updates]
    assert fractions == sorted(fractions)
    assert updates[-1].stage == 'finalisation'
    assert updates[-1].fraction == 1
    assert result.cache_hit is False


def test_progress_continues_without_scene_cuts(tmp_path):
    source = tmp_path / 'long-shot.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
                    '-i', 'color=c=gray:s=320x180:r=24:d=4', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', str(source)], check=True)
    updates = []
    detect_result(source, tmp_path/'cache', .8, analysis_width=64,
                  decode='software', progress=updates.append)
    analysed = [item.processed_seconds for item in updates if item.stage == 'analyse']
    assert analysed
    assert max(analysed) > 0
    assert updates[-1].stage == 'finalisation'


def test_hardware_failure_falls_back(monkeypatch, media, tmp_path):
    calls = []
    original_popen = subprocess.Popen

    def fake_popen(args, **kwargs):
        calls.append(args)
        if '-hwaccel' in args:
            raise OSError('VideoToolbox unavailable')
        return original_popen(args, **kwargs)

    monkeypatch.setattr('remixer.subprocess.Popen', fake_popen)
    result = detect_result(media, tmp_path/'cache', .15, analysis_width=64,
                           decode='auto')
    assert result.actual_decode == 'Logiciel'
    assert result.fallback_reason
    assert any('-hwaccel' in call for call in calls)
    assert len(list((tmp_path/'cache').glob('*.json'))) == 1


def test_cancel_does_not_write_cache(media, tmp_path):
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(AnalysisCancelled):
        detect_result(media, tmp_path/'cache', .15, analysis_width=64,
                      decode='software', cancel_event=cancelled)
    assert not list((tmp_path/'cache').glob('*.json'))


def test_cache_invalidates_when_source_changes(media, tmp_path):
    source = tmp_path / 'mutable.mp4'
    shutil.copyfile(media, source)
    cache = tmp_path / 'cache'
    first = detect_result(source, cache, .15, analysis_width=64, decode='software')
    source.write_bytes(source.read_bytes() + b'changed')
    second = detect_result(source, cache, .15, analysis_width=64, decode='software')
    assert first.cache_hit is False
    assert second.cache_hit is False
    assert len(list(cache.glob('*.json'))) == 2


def test_concurrent_cache_writes_do_not_collide(media, tmp_path):
    cache = tmp_path / 'cache'
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda _: detect_result(media, cache, .15, analysis_width=64, decode='software'),
            range(2)))
    assert all(result.shots == results[0].shots for result in results)
    assert len(list(cache.glob('*.json'))) == 1


def test_video_zip_is_ordered_stored_and_reused(media, tmp_path):
    outputs = []
    for index in range(3):
        path = tmp_path / f'random-{index}.mp4'
        shutil.copyfile(media, path)
        outputs.append(path)
    archive, count, size = prepare_video_zip(outputs, tmp_path, 'batch-1')
    assert count == 3
    assert size == archive.stat().st_size
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist() == ['edit-001.mp4', 'edit-002.mp4', 'edit-003.mp4']
        assert bundle.read('edit-001.mp4') == outputs[0].read_bytes()
        assert all(not name.endswith('.mp4') or 'random' not in name
                   for name in bundle.namelist())
    reused, _, _ = prepare_video_zip(outputs, tmp_path, 'batch-1')
    assert reused == archive
    shutil.copyfile(media, outputs[1])
    outputs[1].touch()
    rebuilt, _, _ = prepare_video_zip(outputs, tmp_path, 'batch-1')
    with zipfile.ZipFile(rebuilt) as bundle:
        assert bundle.read('edit-002.mp4') == outputs[1].read_bytes()


def test_video_zip_isolated_and_rejects_missing_export(media, tmp_path):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    video = first / 'edit.mp4'
    shutil.copyfile(media, video)
    archive, _, _ = prepare_video_zip([video], first, 'one')
    other_video = second / 'edit.mp4'
    shutil.copyfile(media, other_video)
    other, _, _ = prepare_video_zip([other_video], second, 'two')
    assert archive != other
    video.unlink()
    with pytest.raises(RemixError, match='manquant'):
        prepare_video_zip([video], first, 'one')


@pytest.mark.parametrize('mode', ['continuous', 'shuffle', 'external', 'mute'])
def test_render(media, tmp_path, mode):
    clips = [Clip(2, 2., 24), Clip(0, 0., 24)]
    output = tmp_path/f'{mode}.mp4'
    report = render(media, clips, output, 24, mode, soundtrack=media if mode=='external' else None)
    result = probe(output)
    assert abs(result['video_duration']-2) <= 1/24
    assert bool(result['audio']) == (mode!='mute')
    if result['audio']:
        assert abs(result['audio_duration']-2) < .06
    assert report['duration'] == 2
    # Check shuffled visual order: blue first, red second.
    pixels = subprocess.run(['ffmpeg','-v','error','-i',str(output),
        '-vf','scale=1:1','-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'],
        check=True, capture_output=True).stdout
    assert pixels[2] > pixels[0] + 100


def test_external_audio_passages_are_really_continuous(media, tmp_path):
    soundtrack = tmp_path / 'sections.wav'
    subprocess.run([
        'ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
        '-i', 'sine=frequency=440:sample_rate=48000:duration=1',
        '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000:duration=1',
        '-filter_complex', '[0:a][1:a]concat=n=2:v=0:a=1[a]',
        '-map', '[a]', '-c:a', 'pcm_s16le', str(soundtrack)
    ], check=True, capture_output=True)
    clips = [Clip(0, 0, 24)]
    first = render(media, clips, tmp_path/'first.mp4', 24, 'external', 0, soundtrack)
    second = render(media, clips, tmp_path/'second.mp4', 24, 'external', 1, soundtrack)
    assert first['audio_start'] == 0
    assert second['audio_start'] == 1
    def zero_crossings(path):
        pcm = subprocess.run([
            'ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0',
            '-t', '0.8', '-f', 's16le', '-ac', '1', '-ar', '48000', '-'
        ], check=True, capture_output=True).stdout
        samples = list(int.from_bytes(pcm[i:i + 2], 'little', signed=True)
                       for i in range(0, len(pcm) - 1, 2))
        return sum(a * b < 0 for a, b in zip(samples, samples[1:]))
    assert zero_crossings(tmp_path/'second.mp4') > zero_crossings(tmp_path/'first.mp4') * 1.5


def test_audio_errors(media, tmp_path):
    with pytest.raises(RemixError, match='courte'):
        render(media, [Clip(0,0,48)], tmp_path/'too-long.mp4', 24, audio_start=3)
    with pytest.raises(RemixError, match='externe'):
        render(media, [Clip(0,0,24)], tmp_path/'external.mp4', 24, 'external')


def test_missing_audio(media, tmp_path):
    silent = tmp_path/'silent.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-i',str(media),'-map','0:v:0',
                    '-an','-c','copy',str(silent)], check=True)
    with pytest.raises(RemixError, match='Aucun flux audio'):
        render(silent, [Clip(0,0,24)], tmp_path/'bad.mp4', 24)
    render(silent, [Clip(0,0,24)], tmp_path/'ok.mp4', 24, 'mute')


def test_rotation_render(media, tmp_path):
    rotated = tmp_path/'rotated.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-display_rotation:v:0','90','-i',str(media),
                    '-c','copy',str(rotated)], check=True)
    output = tmp_path/'portrait.mp4'
    report = render(rotated, [Clip(0,0,24)], output, 24, 'mute')
    assert (report['width'], report['height']) == (90,160)
    data = probe(output)
    assert (data['video']['width'], data['video']['height']) == (90,160)


def test_different_fps(media, tmp_path):
    output = tmp_path/'fps30.mp4'
    render(media, [Clip(2,2.,30), Clip(0,0.,30)], output, 30, 'continuous')
    assert abs(probe(output)['video_duration']-2) <= 1/30
