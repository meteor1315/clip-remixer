import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remixer import Clip, RemixError, Shot, detect, dimensions, plan, probe, render


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
    shots = detect(media, tmp_path/'cache', .15)
    assert len(shots) == 4
    for i, s in enumerate(shots):
        assert abs(s.start-i) < 1/24
        assert abs(s.end-(i+1)) < 1/24
    assert shots == detect(media, tmp_path/'cache', .15)


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
