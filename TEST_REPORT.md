# Rapport de vérification initiale

- Environnement : Linux, Python 3.12.8, FFmpeg 8.0.1.
- Commande exécutée : `python -m pytest -q --color=no`.
- Résultat : 20 tests réussis, aucun échec (environ 3 secondes).
- Syntaxe des trois fichiers Python vérifiée par `ast.parse`.
- Scripts d’installation et de lancement vérifiés par `bash -n`.

Couverture : reproductibilité et diversité des variantes, durées 15/20 secondes
au niveau planification, limites de plans fractionnaires, exclusions, paramètres
invalides, contraintes impossibles, dimensions/rotation, détection et cache sur
quatre plans synthétiques, rendus MP4 des quatre modes audio, contrôle des durées
vidéo/audio et de la première image, changement de cadence, audio absent/trop court,
rotation via métadonnées et export portrait.

Les rendus d’intégration sont courts (1–2 secondes), sur médias synthétiques.
Non testé : ton clip réel de 4 min 49, macOS Apple Silicon, VideoToolbox,
interface Streamlit interactive/Safari, HDR et sources VFR complexes.
La CI GitHub est fournie, pas encore exécutée au moment de ce rapport.
