# Clip Remixer

Outil personnel local pour macOS Apple Silicon : transformer un clip musical existant
(par exemple 4 min 49) en plusieurs montages aléatoires de 15 ou 20 secondes,
en respectant les changements de plan détectés. Pas de modèle IA, de clé API,
de compte applicatif, d’envoi cloud ou de télémétrie applicative.

## Installation sur Mac M1 / M2 / M3 / M4 et suivants

Prérequis : Git, Python 3.11+ natif ARM64 et FFmpeg 6+ dans le PATH (FFmpeg récent recommandé).
Si Homebrew est déjà installé :

```bash
brew install python@3.12 ffmpeg
git clone https://github.com/meteor1315/clip-remixer.git
cd clip-remixer
PYTHON="$(brew --prefix python@3.12)/bin/python3.12" bash scripts/setup.sh
bash scripts/run.sh
```

Le dépôt est privé : Git doit être authentifié à ton compte GitHub.
Le script n’installe pas Homebrew et n’exécute aucun installateur distant.
L’installation télécharge les dépendances Python ; le traitement vidéo reste local.
L’interface est servie sur http://127.0.0.1:8501. Arrêter avec Ctrl+C.

## Utilisation

1. Indiquer le chemin absolu de la vidéo (recommandé pour les gros fichiers),
   ou importer un fichier. Ne pas entourer le chemin de guillemets.
2. Lancer l’analyse et vérifier la liste des plans. Le seuil bas détecte plus de coupes.
3. Prévisualiser et exclure des plans si nécessaire. Les repères du lecteur sont
   indicatifs et peuvent être arrondis ; le montage utilise les repères précis.
4. Choisir 15/20 secondes, le nombre d’édits, la cadence et les durées des extraits.
5. Choisir une graine : mêmes paramètres et source = mêmes décisions de montage.
6. Sélectionner l’audio : passage continu de la source (par défaut), son des
   extraits mélangés, bande-son externe ou silence. Le passage musical continu
   ne saute pas à chaque coupe visuelle. Choisir son début en secondes.
7. Générer puis consulter les MP4 et plans de montage JSON.

Chaque plan détecté est utilisé au maximum une fois par édit. Plusieurs édits
peuvent réutiliser un plan, mais leurs timelines doivent être distinctes.
Les plans trop courts pour le minimum choisi sont ignorés. Un plan touchant une
plage exclue est exclu entièrement. En cas de contraintes impossibles, le logiciel
refuse le montage au lieu de répéter silencieusement des plans. La recherche est
bornée : pour des contraintes très serrées, elle peut demander de les assouplir.

## Architecture et choix techniques

- `remixer.py` : ffprobe, analyse FFmpeg du score `scene`, cache SHA-256,
  planification déterministe en nombres de frames, export FFmpeg.
- `app.py` : interface Streamlit en français, uniquement sur la boucle locale.
- `tests/` : tests unitaires et intégration avec médias synthétiques.
- `scripts/` : installation venv et lancement macOS.

La détection utilise directement FFmpeg plutôt que PySceneDetect : une dépendance
native en moins sur Mac. Les extraits restent dans les limites détectées ; cela
ne garantit pas que chaque vraie coupe ait été détectée. Les décisions sont
alignées sur la cadence de sortie ; précision visée à une image près.
Export H.264/AAC MP4, format d’origine conservé à l’arrondi pair près, définition
limitée à 1920 pour le grand côté et 1080 pour le petit côté sans agrandissement
(sauf correction du ratio de pixel). L’option 9:16 recadre au centre en 1080x1920,
sans suivi de sujet. Rotation des vidéos prise en compte par FFmpeg et le calcul
des dimensions ; codecs vidéo/image de couverture exclus du choix de piste.
VideoToolbox est facultatif : repli CPU en cas d’absence ou d’échec.

## Fichiers, mémoire et confidentialité

Sessions et cache : `~/Library/Caches/clip-remixer/`. Chaque session a un dossier
isolé ; les exports sont conservés après fermeture. Après avoir copié les exports
utiles et arrêté l’application, supprimer les dossiers de session inutiles avec
le Finder. Le sous-dossier `detection` ne contient que des repères de plans.
Les intermédiaires FFmpeg sont temporaires et nettoyés en sortie normale/erreur
(une interruption forcée peut laisser des fichiers temporaires système).
Ne jamais mettre les médias dans le dépôt Git.

Streamlit garde les imports en mémoire : pour une grosse vidéo utiliser le chemin
local. Limite d’import : 1024 Mo, modifiable dans `.streamlit/config.toml`.
La préparation du téléchargement MP4 charge aussi cet export en mémoire ; les
fichiers sont déjà disponibles sur disque, sans passer par ce bouton.
La lecture dans le navigateur peut également consommer de la mémoire.
Ne pas exposer le serveur sur le réseau : pas d’authentification intégrée.

## Limites et dépannage

- Flashs, fondus, stroboscopes, mouvements et cuts subtils peuvent tromper le seuil.
- Pas de classement artistique, de compréhension des images, de beat-sync,
  de génération IA ni de reproduction de l’algorithme interne de Flowstage.
- Tests sur médias synthétiques ; VFR complexe, HDR, pistes avec décalage initial
  et formats exotiques ne sont pas garantis. Pour ces cas, normaliser en MP4 SDR CFR.
- CPU : le rendu peut être lent. VideoToolbox peut aider, sans garantie de vitesse.
- `ffmpeg introuvable` : vérifier `which ffmpeg` et `brew install ffmpeg`.
- Dépendances ARM64 : éviter un Python Intel via Rosetta ; vérifier
  `python3 -c "import platform; print(platform.machine())"` (attendu : arm64).
- Aucun audio ou audio trop court : choisir muet, une autre bande-son ou un début antérieur.
- Respecter les droits des vidéos et musiques utilisées.

## Tests

```bash
.venv/bin/python -m pytest -q
```

La CI est sous Linux avec FFmpeg. Elle ne valide pas le matériel Apple Silicon,
VideoToolbox, l’interface dans Safari ni ton clip réel. Consulter `TEST_REPORT.md`
pour les tests effectivement exécutés lors de la construction initiale.
