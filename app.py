"""French local UI. Launch with bash scripts/run.sh."""
import json
import queue
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import streamlit as st
from remixer import (AnalysisCancelled, RemixError, detect_result,
                     plan, plan_audio_passages, prepare_video_zip, probe, render)

st.set_page_config(page_title='Clip Remixer', page_icon='🎬', layout='wide')
st.title('Clip Remixer — montage local')
st.caption('Détection des plans → mélange sous contraintes → édits de 15 / 20 secondes. Aucune API IA.')
root = Path.home() / 'Library' / 'Caches' / 'clip-remixer'
root.mkdir(parents=True, exist_ok=True)
if 'workspace' not in st.session_state:
    st.session_state.workspace = str(root / uuid.uuid4().hex)
workspace = Path(st.session_state.workspace)
workspace.mkdir(parents=True, exist_ok=True)
st.caption(f'Fichiers de cette session : {workspace}')
st.info('Pour une grosse vidéo, privilégier le chemin local : aucun téléversement en mémoire. '
        'Les fichiers importés et exports restent sur ce Mac. Supprimer le dossier de session après usage.')

def local_file(label, key, extensions):
    method = st.radio(label, ['Chemin local (recommandé)', 'Importer un fichier'], key=key+'_method', horizontal=True)
    if method.startswith('Chemin'):
        value = st.text_input('Chemin absolu — sans guillemets', key=key+'_path')
        if not value.strip():
            return None
        path = Path(value.strip()).expanduser()
        if not path.is_absolute() or not path.is_file():
            st.warning('Indiquer un chemin absolu vers un fichier existant.')
            return None
        return path.resolve()
    upload = st.file_uploader(label, type=extensions, key=key+'_upload')
    if upload is None:
        return None
    # Streamlit itself buffers uploads; local-path mode avoids this limitation.
    token = (upload.file_id, upload.size)
    if st.session_state.get(key+'_token') != token:
        path = workspace / (key + Path(upload.name).suffix.lower())
        upload.seek(0)
        with path.open('wb') as target:
            while chunk := upload.read(4 * 1024 * 1024):
                target.write(chunk)
        st.session_state[key+'_stored'] = str(path)
        st.session_state[key+'_token'] = token
    return Path(st.session_state[key+'_stored'])

source = local_file('Vidéo source', 'source', ['mp4', 'mov', 'mkv', 'm4v'])
threshold = st.slider('Seuil de changement de plan (bas = plus de coupes)', .05, .8, .3, .01)
analysis_mode = st.radio('Résolution d’analyse', ['Réduite (largeur maximale 640 px)',
                                                   'Originale'], horizontal=True)
analysis_width = None if analysis_mode == 'Originale' else st.number_input(
    'Largeur maximale d’analyse (pixels)', 2, 4096, 640, 2)
decode_label = st.selectbox('Décodage de l’analyse', ['Auto', 'Logiciel', 'VideoToolbox'])
decode_mode = {'Auto': 'auto', 'Logiciel': 'software', 'VideoToolbox': 'videotoolbox'}[decode_label]


def _analysis_worker(path, threshold_value, width, decode_value, updates, cancelled):
    return detect_result(path, root / 'detection', threshold_value, width, decode_value,
                         updates.put, cancelled)


if 'analysis_executor' not in st.session_state:
    st.session_state.analysis_executor = ThreadPoolExecutor(max_workers=1)
if 'analysis_job' not in st.session_state:
    st.session_state.analysis_job = None

if source and st.button('1. Analyser les changements de plan', type='primary',
                        disabled=st.session_state.analysis_job is not None):
    updates = queue.Queue()
    cancelled = threading.Event()
    future = st.session_state.analysis_executor.submit(
        _analysis_worker, source, threshold, analysis_width, decode_mode, updates, cancelled)
    st.session_state.analysis_job = {
        'future': future, 'updates': updates, 'cancelled': cancelled,
        'path': str(source), 'threshold': threshold, 'width': analysis_width,
        'decode': decode_mode,
        'size': source.stat().st_size, 'mtime': source.stat().st_mtime_ns,
    }
    st.rerun()


def _show_analysis_job():
    job = st.session_state.analysis_job
    if job is None:
        return
    future: Future = job['future']
    latest = job.get('latest')
    while True:
        try:
            latest = job['updates'].get_nowait()
        except queue.Empty:
            break
    if latest:
        job['latest'] = latest
        mode = latest.decode_mode
        status = f'{latest.stage} — décodage {mode}'
        if latest.fallback_reason:
            status += f' (repli logiciel : {latest.fallback_reason})'
        st.write(status)
        st.progress(max(0., min(1., latest.fraction)))
        st.caption(f'{latest.processed_seconds:.2f} / {latest.total_seconds:.2f} s vidéo '
                   f'— temps écoulé {latest.elapsed_seconds:.1f} s'
                   + (f' — restant estimé {latest.eta_seconds:.1f} s'
                      if latest.eta_seconds is not None else ''))
    if future.done():
        try:
            result = future.result()
            stat = source.stat()
            if (str(source) != job['path'] or stat.st_size != job['size']
                    or stat.st_mtime_ns != job['mtime']):
                raise RemixError('La source a changé pendant l’analyse ; résultat ignoré.')
            st.session_state.analysis = {
                'path': job['path'], 'size': stat.st_size, 'mtime': stat.st_mtime_ns,
                'threshold': job['threshold'], 'analysis_width': job['width'],
                'decode': job['decode'], 'shots': result.shots,
                'cache_hit': result.cache_hit, 'actual_decode': result.actual_decode,
                'fallback_reason': result.fallback_reason,
            }
            st.session_state.pop('outputs', None)
            st.session_state.analysis_job = None
            st.rerun()
        except (AnalysisCancelled, RemixError, OSError) as error:
            st.session_state.analysis_job = None
            st.error(str(error))
    else:
        if st.button('Annuler l’analyse', type='secondary'):
            job['cancelled'].set()
            st.info('Annulation demandée : FFmpeg est arrêté proprement…')
        st.caption('Une seule analyse peut être active dans cette session.')


if st.session_state.analysis_job is not None:
    if hasattr(st, 'fragment'):
        _show_analysis_job = st.fragment(run_every='0.5s')(_show_analysis_job)
    _show_analysis_job()

analysis = st.session_state.get('analysis')
valid = (source and analysis and analysis['path'] == str(source)
         and analysis['threshold'] == threshold
         and analysis.get('analysis_width') == analysis_width
         and analysis.get('decode') == decode_mode)
if valid:
    stat = source.stat()
    valid = stat.st_size == analysis['size'] and stat.st_mtime_ns == analysis['mtime']
if not valid:
    st.caption('Importer une vidéo puis lancer l’analyse. Relancer après modification de la source ou du seuil.')
    st.stop()
shots = analysis['shots']
st.success(f'{len(shots)} plans détectés. Vérifier les coupes : flashs et fondus peuvent perturber la détection.')
if analysis.get('cache_hit'):
    st.info('Analyse chargée depuis le cache local.')
st.caption(f"Décodage effectivement utilisé : {analysis.get('actual_decode', 'Logiciel')}"
           + (f" — repli : {analysis['fallback_reason']}"
              if analysis.get('fallback_reason') else ''))
if analysis_width is not None:
    st.caption('Les scores et plans en résolution réduite peuvent différer de l’originale ; '
               'ajuster le seuil si nécessaire.')
st.dataframe([{'Plan':s.id, 'Début (s)':round(s.start, 3), 'Fin (s)':round(s.end, 3),
               'Durée (s)':round(s.end-s.start, 3)} for s in shots], hide_index=True)
with st.expander('Prévisualiser un plan / exclure des plans'):
    selected = st.selectbox('Plan à prévisualiser', [s.id for s in shots])
    shot = next(s for s in shots if s.id == selected)
    st.caption('Prévisualisation indicative : le lecteur peut arrondir les repères. Le rendu utilise les valeurs précises.')
    st.video(str(source), start_time=shot.start, end_time=shot.end)
    excluded = st.multiselect('Plans exclus', [s.id for s in shots], key='excluded_shots')
    ranges_text = st.text_input('Plages à exclure (secondes) : 0-10; 270-289', '')
    st.caption('Un plan touchant une plage exclue est exclu entièrement.')
a, b, c = st.columns(3)
duration = a.selectbox('Durée finale (secondes)', [15, 20])
count = b.number_input('Nombre de variantes', 5, 15, 5)
fps = c.selectbox('Cadence de sortie', [24, 25, 30, 60], index=2)
a, b, c = st.columns(3)
minimum = a.number_input('Extrait minimum (secondes)', .1, 10., 1., .1)
maximum = b.number_input('Extrait maximum (secondes)', .1, 20., 3., .1)
seed = c.number_input('Graine aléatoire', 0, 2147483647, 42)
audio_labels = {'Passage musical continu (source)':'continuous',
                'Son des extraits mélangés':'shuffle', 'Bande-son externe':'external', 'Muet':'mute'}
mode = audio_labels[st.selectbox('Audio', list(audio_labels))]
audio_start = st.number_input('Début du passage audio (secondes)', 0., value=0., step=1.) if mode in {'continuous','external'} else 0.
soundtrack = local_file('Bande-son externe', 'audio', ['wav', 'mp3', 'm4a', 'flac', 'aac']) if mode == 'external' else None
audio_distribution_labels = {
    'Même passage pour tous les édits': 'same',
    'Passages successifs sans chevauchement': 'successive',
    'Passages aléatoires différents': 'random',
}
audio_distribution = ('same' if mode not in {'continuous', 'external'} else
                       audio_distribution_labels[st.selectbox(
                           'Répartition des passages audio',
                           list(audio_distribution_labels), index=2,
                           key='audio_distribution')])
audio_end_enabled = (mode in {'continuous', 'external'} and
                     st.checkbox('Définir une borne de fin audio', value=False,
                                 key='audio_end_enabled'))
audio_end = (st.number_input('Fin de la plage audio (secondes)', min_value=0.,
                             value=60., step=1., key='audio_end')
             if audio_end_enabled else None)
audio_avoid_overlap = (audio_distribution == 'random' and
                       st.checkbox('Éviter le chevauchement des passages',
                                   value=False, key='audio_avoid_overlap'))
audio_minimum_gap = (st.number_input('Écart minimal entre débuts (secondes)',
                                     min_value=0., value=5., step=.5,
                                     key='audio_minimum_gap')
                     if audio_distribution == 'random' and not audio_avoid_overlap else 0.)
audio_seed = (st.number_input('Graine audio', 0, 2147483647, 42,
                              key='audio_seed')
              if audio_distribution == 'random' else None)
vertical = st.checkbox('Recadrer en 9:16 (1080 × 1920, recadrage central, pas de suivi du sujet)')
hardware = st.checkbox('Utiliser VideoToolbox si disponible (sinon repli CPU)')

def _parse_ranges(value):
    ranges = []
    for item in value.split(';'):
        if item.strip():
            left, right = item.split('-')
            ranges.append((float(left), float(right)))
    return ranges


preview_config = {
    'mode': mode, 'distribution': audio_distribution, 'start': float(audio_start),
    'end': audio_end, 'avoid_overlap': audio_avoid_overlap,
    'minimum_gap': float(audio_minimum_gap),
    'audio_seed': None if audio_seed is None else int(audio_seed),
    'duration': int(duration), 'fps': int(fps), 'count': int(count),
    'minimum': float(minimum), 'maximum': float(maximum), 'seed': int(seed),
}
prepared = st.session_state.get('audio_batch')
if prepared and prepared['config'] != preview_config:
    st.session_state.pop('audio_batch', None)
    prepared = None

if st.button('2. Préparer les édits', type='primary'):
    st.session_state.pop('outputs', None)
    try:
        ranges = _parse_ranges(ranges_text)
        variants = plan(shots, duration, fps, minimum, maximum, int(count), int(seed),
                        set(excluded), ranges)
        audio_path = (Path(soundtrack) if mode == 'external' else source) if mode != 'mute' else None
        if mode != 'mute':
            if audio_path is None:
                raise RemixError('Sélectionner un fichier audio externe.')
            audio_info = probe(audio_path)
            if not audio_info['audio']:
                raise RemixError('Aucun flux audio : choisir le mode muet ou un autre fichier.')
            audio_duration = audio_info.get('audio_duration', 0)
            passages = plan_audio_passages(
                [sum(clip.frames for clip in clips) / int(fps) for clips in variants],
                audio_duration, float(audio_start), audio_end, audio_distribution,
                int(audio_seed or 0), audio_avoid_overlap, float(audio_minimum_gap))
        else:
            passages = [None] * len(variants)
        st.session_state.audio_batch = {
            'config': preview_config, 'variants': variants, 'passages': passages,
            'ranges': ranges, 'audio_filename': audio_path.name if audio_path else None,
        }
        st.rerun()
    except (RemixError, OSError, ValueError) as error:
        st.error(str(error))

prepared = st.session_state.get('audio_batch')
if prepared:
    st.subheader('Récapitulatif des passages audio')
    preview_rows = []
    for index, passage in enumerate(prepared['passages'], 1):
        preview_rows.append({
            'Variante': index,
            'Début audio (s)': None if passage is None else round(passage.start, 6),
            'Fin audio (s)': None if passage is None else round(passage.end, 6),
            'Durée (s)': round(sum(c.frames for c in prepared['variants'][index - 1]) / fps, 6),
        })
    st.dataframe(preview_rows, hide_index=True)
    if st.button('3. Lancer les exports', type='primary'):
        st.session_state.pop('outputs', None)
        try:
            batch = workspace / uuid.uuid4().hex
            batch.mkdir()
            results = []
            bar = st.progress(0.)
            for i, clips in enumerate(prepared['variants']):
                output = batch / f'edit-{i+1:03d}.mp4'
                st.write(f'Export {i+1}/{len(prepared["variants"])}')
                passage = prepared['passages'][i]
                report = render(source, clips, output, fps, mode,
                                passage.start if passage else 0,
                                soundtrack, vertical, hardware,
                                lambda p, i=i: bar.progress((i+p)/len(prepared['variants'])))
                report.update(seed=int(seed), variant=i+1, threshold=threshold,
                              source_name=source.name, exclusions=excluded,
                              excluded_ranges=prepared['ranges'],
                              audio_distribution=audio_distribution,
                              audio_start=None if passage is None else passage.start,
                              audio_end=None if passage is None else passage.end,
                              audio_duration=None if passage is None else passage.duration,
                              audio_seed=audio_seed,
                              audio_range_start=audio_start if mode != 'mute' else None,
                              audio_range_end=audio_end if mode != 'mute' else None,
                              audio_avoid_overlap=audio_avoid_overlap if mode != 'mute' else None,
                              audio_minimum_gap=audio_minimum_gap if mode != 'mute' else None,
                              audio_filename=prepared['audio_filename'])
                decision = output.with_suffix('.json')
                decision.write_text(json.dumps(report, indent=2, ensure_ascii=False))
                results.append((str(output), str(decision), report['hardware_fallback']))
                st.session_state.outputs = results.copy()
            st.session_state.pop('audio_batch', None)
            st.success('Exports terminés et enregistrés sur ce Mac.')
        except (RemixError, OSError, ValueError) as error:
            st.error(str(error))
outputs = st.session_state.get('outputs', [])
if outputs:
    st.subheader('Téléchargement du lot')
    try:
        video_paths = [Path(output) for output, _, _ in outputs]
        archive, video_count, archive_size = prepare_video_zip(
            video_paths, video_paths[0].parent, video_paths[0].parent.name)
        st.caption(f'{video_count} vidéos incluses — archive : {archive_size / (1024 ** 2):.1f} Mo')
        download_limit = 512 * 1024 * 1024
        if archive_size <= download_limit:
            st.download_button(
                'Télécharger toutes les vidéos (.zip)', archive.read_bytes(),
                file_name=archive.name, mime='application/zip',
                key=f'all-videos-{archive.name}')
        else:
            st.warning(
                f'L’archive dépasse la limite de téléchargement en mémoire '
                f'({download_limit / (1024 ** 2):.0f} Mo). Elle est disponible localement : '
                f'{archive}')
    except (RemixError, OSError) as error:
        st.error(f'Lot incomplet : {error}')

for output, decision, fallback in outputs:
    path = Path(output)
    st.subheader(path.name)
    if fallback:
        st.warning('VideoToolbox indisponible ou en échec : export réalisé sur CPU.')
    st.video(str(path))
    st.caption(str(path))
    st.download_button('Télécharger le plan de montage JSON', Path(decision).read_bytes(),
                       file_name=Path(decision).name, mime='application/json', key=decision)
