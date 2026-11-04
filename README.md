# Clip Remixer

Outil personnel local pour macOS Apple Silicon : transformer un clip musical existant
(par exemple 4 min 49) en plusieurs montages aléatoires de 15 ou 20 secondes,
en respectant les changements de plan détectés. Pas de modèle IA, de clé API,
de compte applicatif, d’envoi cloud ou de télémétrie applicative.

## Installation sur Mac M1 / M2 / M3 / M4 et suivants

### Prérequis

- macOS Apple Silicon (`arm64`) ;
- Git et les outils de ligne de commande Xcode ;
- Python 3.11 ou supérieur, natif Apple Silicon ;
- FFmpeg 6 ou supérieur, avec `ffmpeg` et `ffprobe` dans le `PATH` ;
- Homebrew recommandé pour installer les outils.

Vérifier l’environnement :

```bash
uname -m
git --version
python3 --version
ffmpeg -version
ffprobe -version
```

`uname -m` doit afficher `arm64`.

### Installation avec Homebrew

Installer Homebrew depuis [brew.sh](https://brew.sh/) s’il n’est pas déjà
disponible, puis installer les prérequis :

```bash
brew update
brew install git python@3.12 ffmpeg
```

Cloner le dépôt public et installer Clip Remixer :

```bash
git clone https://github.com/meteor1315/clip-remixer.git
cd clip-remixer
PYTHON="$(brew --prefix python@3.12)/bin/python3.12" bash scripts/setup.sh
bash scripts/run.sh
```

### Clonage avec GitHub CLI

GitHub CLI (`gh`) est optionnel. Il peut être installé avec Homebrew :

```bash
brew install gh
gh repo clone meteor1315/clip-remixer
cd clip-remixer
PYTHON="$(brew --prefix python@3.12)/bin/python3.12" bash scripts/setup.sh
bash scripts/run.sh
```

Pour un dépôt public, aucune authentification GitHub n’est nécessaire pour
cloner en HTTPS. L’installation de GitHub CLI n’est donc pas obligatoire.

### Installation sans Homebrew

Installer Git, Python arm64 et FFmpeg depuis leurs sites officiels, puis vérifier
que leurs commandes sont accessibles dans le `PATH` :

```bash
git clone https://github.com/meteor1315/clip-remixer.git
cd clip-remixer
PYTHON=python3 bash scripts/setup.sh
bash scripts/run.sh
```

Le script n’installe pas Homebrew et n’exécute aucun installateur distant.
L’installation télécharge les dépendances Python ; le traitement vidéo reste local.
L’interface est servie sur http://127.0.0.1:8501. Arrêter avec Ctrl+C.

## Utilisation

1. Indiquer le chemin absolu de la vidéo (recommandé pour les gros fichiers),
   ou importer un fichier. Ne pas entourer le chemin de guillemets.
2. Lancer l’analyse et vérifier la liste des plans. Le seuil bas détecte plus de coupes.
3. Prévisualiser et exclure des plans si nécessaire. Les repères du lecteur sont
   indicatifs et peuvent être arrondis ; le montage utilise les repères précis.
4. Choisir la résolution d’analyse (réduite à 640 px par défaut, sans agrandir
   une petite source, ou originale) et son décodage (Auto, Logiciel ou
   VideoToolbox). La cadence temporelle n’est pas sous-échantillonnée.
   Une analyse réduite peut produire des scores et des plans différents :
   ajuster le seuil si nécessaire.
5. Attendre la progression réelle (temps vidéo traité, temps écoulé et estimation
   stable). Le bouton d’annulation arrête FFmpeg et ne met pas son résultat en cache.
6. Choisir 15/20 secondes, 5 à 15 édits, la cadence et les durées des extraits.
7. Choisir une graine : mêmes paramètres et source = mêmes décisions de montage.
8. Sélectionner l’audio : passage continu de la source (par défaut), son des
   extraits mélangés, bande-son externe ou silence. Pour la source et une bande-son
   externe, choisir la répartition des passages : même passage, passages
   successifs, ou passages aléatoires différents. Les deux derniers modes
   utilisent la plage audio définie et la graine audio, et le mode aléatoire peut
   interdire le chevauchement ou imposer un écart minimal entre débuts.
   Le passage musical reste continu pendant tout l’édit, sans changement à
   chaque coupe vidéo.
9. Cliquer sur « Préparer les édits » pour figer la planification vidéo et audio,
   vérifier le tableau récapitulatif, puis lancer les exports. Les JSON indiquent
   les bornes, la durée, la graine et le nom du fichier audio (sans chemin).
10. Au-dessus des résultats, cliquer sur « Télécharger toutes les vidéos (.zip) »
    pour récupérer le lot affiché en une seule archive. L’archive contient
    `edit-001.mp4`, `edit-002.mp4`, etc., utilise `ZIP_STORED` et est réutilisée
    tant que les exports du lot restent inchangés. Les JSON restent téléchargeables
    individuellement ; les téléchargements MP4 individuels ne sont pas proposés.

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
Le réglage de décodage est indépendant de VideoToolbox utilisé éventuellement
pour encoder les exports. Le mode effectivement confirmé par FFmpeg et la raison
d’un repli sont affichés ; aucun gain de vitesse n’est garanti.

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
Le ZIP est d’abord construit sur disque de manière atomique, sans recompresser les
MP4. La version actuelle de Streamlit charge ensuite l’archive dans la mémoire du
processus pour `st.download_button` ; une limite de 512 Mo est appliquée. Pour un
lot plus gros, l’archive complète reste disponible dans le dossier de session
affiché par l’application et peut être copiée localement depuis le Finder.
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
- La durée et les bornes audio sont vérifiées sur le flux audio sélectionné,
  pas sur la durée vidéo. Les plages VFR ou avec timestamps atypiques restent
  soumises aux limites documentées de l’analyse.
- Respecter les droits des vidéos et musiques utilisées.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Benchmark d’analyse (cache temporaire isolé, sans média ni résultat personnel
dans le dépôt) :

```bash
.venv/bin/python scripts/benchmark_analysis.py /chemin/absolu/vers/video.mp4
```

Le benchmark compare A (originale/logiciel), B (640 px/logiciel) et C
(640 px/VideoToolbox). Il sépare le coût d’empreinte, mesure la durée d’analyse,
compare les repères temporels des coupes et indique le mode réellement utilisé
ou le repli. C doit être exécuté sur le Mac concerné pour valider VideoToolbox ;
les mocks Linux ne constituent pas cette validation.

La CI est sous Linux avec FFmpeg. Elle ne valide pas le matériel Apple Silicon,
VideoToolbox, l’interface dans Safari ni ton clip réel. Consulter `TEST_REPORT.md`
pour les tests effectivement exécutés lors de la construction initiale.
