# Rapport de vérification

- Environnement : Linux, Python 3.12.8, FFmpeg 8.0.1.
- Commande exécutée initialement : `python -m pytest -q --color=no`.
- Résultat initial : 20 tests réussis, aucun échec.
- Après optimisation : `./.venv/bin/python -m pytest -q` — 34 tests réussis,
  aucun échec (environ 5,5 secondes).
- Syntaxe des trois fichiers Python vérifiée par `ast.parse`.
- Scripts d’installation et de lancement vérifiés par `bash -n`.

Couverture : archive ZIP ordonnée et `ZIP_STORED`, réutilisation/invalidation,
isolation des lots, export manquant, planification audio (même passage, successif, aléatoire reproductible,
bornes, écart minimal, absence de chevauchement et contraintes impossibles),
intégration de sections audio synthétiques différenciables, résolution originale/réduite,
progression monotone et finalisation, progression sur une longue séquence sans coupe,
repli VideoToolbox simulé, annulation sans cache partiel, reproductibilité et diversité des variantes, durées 15/20 secondes
au niveau planification, limites de plans fractionnaires, exclusions, paramètres
invalides, contraintes impossibles, dimensions/rotation, détection et cache sur
quatre plans synthétiques, rendus MP4 des quatre modes audio, contrôle des durées
vidéo/audio et de la première image, changement de cadence, audio absent/trop court,
rotation via métadonnées et export portrait.

Les rendus d’intégration sont courts (1–2 secondes), sur médias synthétiques.
Non testé ici : ton clip réel de 4 min 49, macOS Apple Silicon, VideoToolbox réel,
interface Streamlit interactive/Safari, HDR et sources VFR complexes.
La branche VideoToolbox est testée par mock sous Linux ; elle doit être mesurée
réellement sur le Mac. La CI GitHub est fournie, mais n’est pas une validation
du matériel Apple.
