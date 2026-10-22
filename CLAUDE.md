# Image Tool

## Project

Outil interne Windows (usage perso → déploiement entreprise prévu) qui automatise des tâches de "design" répétitives : renommage de fichiers, mise en page/impression de repères numérotés papier pour audioguides musée (GuideMark), génération procédurale d'images numérotées (ImageGen), plus 10 autres wizards occasionnels (compress, resize, convert, PDF, HTML, clipboard, liens courts...).

## Stack

Python 3.13 + Tkinter (GUI), Pillow (image), pytest (tests). Pas de dépendance web/serveur. Packagé en `.exe` via PyInstaller pour distribution Windows.

## Environnement

Un seul environnement : poste local Windows de l'utilisateur. Pas de staging/prod — le livrable est un `.exe` distribué à la main. `.venv/` local au projet (non tracké).

## Chemins importants

- `ImageTool.py` — **monolithe legacy**, 10 500+ lignes, encore la source de vérité pour tout ce qui n'a pas été migré. En cours de modularisation progressive vers `app/` (voir Migration ci-dessous) — ne pas dupliquer du code déjà migré, importer depuis `app/` à la place.
- `app/` — nouveaux modules extraits du monolithe (theme, i18n, ui_kit, engines, widgets, wizards). C'est la cible de la refonte : tout code neuf ou migré va ici, pas dans `ImageTool.py`.
- `home/` — futur point d'entrée UI (`HomeScreen`), pas encore branché (`HomeScreen` vit encore dans `ImageTool.py`).
- `tests/` — tests pytest, un fichier par module `app/engines/*`.
- `TourTracker.py`, `export_worker.py`, `LAUNCH TRACKER.bat` — projet séparé et indépendant, sans lien avec ImageTool. Ne pas mélanger.
- Fichiers `_*.json` à la racine — données/config utilisateur générées à l'exécution (presets, cache polices). Ne pas éditer à la main sauf pour du debug ponctuel.
- `_lrw_config.json` — **contient un token en clair** (Cloudflare Worker). Jamais tracké par git, doit le rester (voir `.gitignore`).

## Migration en cours (architecture)

Refonte progressive du monolithe vers une architecture modulaire, **sans big-bang** : `ImageTool.py` doit rester lançable à chaque étape. Pattern : extraire un bloc vers `app/...`, puis remplacer le code original dans `ImageTool.py` par un `from app.xxx import ...`. Voir `PROJECT_STATUS.md` pour l'état d'avancement exact.

## Core rules

- **Ne jamais** committer/pousser sans demande explicite de l'utilisateur.
- **Aucun secret en clair** dans le code, la config trackée, ou les fichiers Markdown — voir `_lrw_config.json` en exemple de ce qu'il ne faut PAS committer.
- Ne pas réintroduire de `pip install` silencieux au runtime (pattern legacy trouvé dans PDFExtractorWizard/HtmlFormatterWizard, à corriger un jour, pas à copier).
- Ne pas ajouter de nouveaux `except: pass` ou `except Exception: pass` — logger l'erreur au minimum. Le monolithe en a beaucoup (dette connue), ne pas en ajouter de nouveaux.
- GuideMark (mise en page papier) est utilisé pour de l'impression envoyée à des clients/musées — toute modification de son moteur de calcul (mm/px) doit être vérifiée avec un test de non-régression, pas seulement "à l'œil".
- Toute nouvelle couleur/police dans l'UI doit venir de `app/theme.py` (constantes `BG*`/`FG*`/`ACCENT*`/`FONT_*`) — jamais de valeur hex ou de tuple de police codé en dur dans un wizard.

## Workflow

```
# Lancer l'app
.venv\Scripts\python.exe ImageTool.py

# Installer les dépendances
.venv\Scripts\python.exe -m pip install -r requirements.txt

# Lancer les tests
.venv\Scripts\python.exe -m pytest tests/ -v
```

Pas de build/CI configuré. Le `.spec` PyInstaller n'existe plus dans `installer/` (seul l'ancien `.exe` compilé y est) — à reconstituer si un nouveau packaging est demandé.

## Efficacité

- Tâche simple (CSS/texte/petit fix localisé) → lecture ciblée + Grep/Glob directs, pas de subagent.
- Réserver les subagents (Explore/Agent/Plan) aux explorations larges ou vraiment multi-fichiers.
- Ne pas relancer l'app après un changement purement visuel/texte sauf demande explicite — laisser l'utilisateur vérifier.
- Avant une modification structurelle notable (nouveau module, refonte d'un wizard), utiliser le mode plan.
