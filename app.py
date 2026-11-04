"""French local UI. Launch with bash scripts/run.sh."""
import json
import uuid
from pathlib import Path
import streamlit as st
from remixer import RemixError, detect, plan, probe, render

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
if source and st.button('1. Analyser les changements de plan', type='primary'):
    try:
        with st.spinner('Analyse locale : décodage de la vidéo…'):
            shots = detect(source, root / 'detection', threshold)
        stat = source.stat()
        st.session_state.analysis = {'path':str(source), 'size':stat.st_size,
            'mtime':stat.st_mtime_ns, 'threshold':threshold, 'shots':shots}
        st.session_state.pop('outputs', None)
    except (RemixError, OSError) as error:
        st.error(str(error))
analysis = st.session_state.get('analysis')
valid = source and analysis and analysis['path'] == str(source) and analysis['threshold'] == threshold
if valid:
    stat = source.stat()
    valid = stat.st_size == analysis['size'] and stat.st_mtime_ns == analysis['mtime']
if not valid:
    st.caption('Importer une vidéo puis lancer l’analyse. Relancer après modification de la source ou du seuil.')
    st.stop()
shots = analysis['shots']
st.success(f'{len(shots)} plans détectés. Vérifier les coupes : flashs et fondus peuvent perturber la détection.')
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
count = b.number_input('Nombre de variantes', 1, 100, 5)
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
vertical = st.checkbox('Recadrer en 9:16 (1080 × 1920, recadrage central, pas de suivi du sujet)')
hardware = st.checkbox('Utiliser VideoToolbox si disponible (sinon repli CPU)')
if st.button('2. Générer les édits', type='primary'):
    st.session_state.pop('outputs', None)
    try:
        ranges = []
        for value in ranges_text.split(';'):
            if value.strip():
                left, right = value.split('-')
                ranges.append((float(left), float(right)))
        variants = plan(shots, duration, fps, minimum, maximum, int(count), int(seed), set(excluded), ranges)
        batch = workspace / uuid.uuid4().hex
        batch.mkdir()
        results = []
        bar = st.progress(0.)
        for i, clips in enumerate(variants):
            output = batch / f'edit-{i+1:03d}.mp4'
            st.write(f'Export {i+1}/{len(variants)}')
            report = render(source, clips, output, fps, mode, audio_start, soundtrack,
                            vertical, hardware,
                            lambda p, i=i: bar.progress((i+p)/len(variants)))
            report.update(seed=int(seed), variant=i+1, threshold=threshold,
                          source_name=source.name, exclusions=excluded, excluded_ranges=ranges)
            decision = output.with_suffix('.json')
            decision.write_text(json.dumps(report, indent=2, ensure_ascii=False))
            results.append((str(output), str(decision), report['hardware_fallback']))
            st.session_state.outputs = results.copy()
        st.success('Exports terminés et enregistrés sur ce Mac.')
    except (RemixError, OSError, ValueError) as error:
        st.error(str(error))
for output, decision, fallback in st.session_state.get('outputs', []):
    path = Path(output)
    st.subheader(path.name)
    if fallback:
        st.warning('VideoToolbox indisponible ou en échec : export réalisé sur CPU.')
    st.video(str(path))
    st.caption(str(path))
    st.download_button('Télécharger le plan de montage JSON', Path(decision).read_bytes(),
                       file_name=Path(decision).name, mime='application/json', key=decision)
    with st.expander('Télécharger le MP4 (charge cet export en mémoire)'):
        if st.checkbox('Préparer le téléchargement', key=output+'_prepare'):
            st.download_button('Télécharger le MP4', path.read_bytes(), file_name=path.name,
                               mime='video/mp4', key=output)
