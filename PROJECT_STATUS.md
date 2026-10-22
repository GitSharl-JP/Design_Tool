# Project Status

## Current state

Trois chantiers menés sur le monolithe `ImageTool.py` :
1. **Architecture** — extraction progressive vers `app/` (theme, i18n, ui_kit, widgets, engines, wizards), coexistence garantie à chaque étape.
2. **Design UI/UX GuideMark** — terminé (voir Recently completed), socle réutilisable pour le reste de l'app.
3. **Macro clic-droit Rename** — terminé en mode dev (registre HKCU, aiguillage, 2 écrans) ; reste à valider en conditions réelles avec un exe packagé (voir Next).

L'app se lance et fonctionne normalement (`.venv\Scripts\python.exe ImageTool.py`). Le clic-droit "Renommer avec Image Tool" est actuellement enregistré dans le registre de cette machine et pointe vers l'environnement Python de dev (pas un exe) — à garder à l'esprit si l'app est déplacée ou l'environnement recréé.

## Recently completed

- **Bug corrigé** : dérive ~3mm à l'impression GuideMark (double arrondi mm→px) — validé sur preset réel.
- **Architecture** : `requirements.txt`, `app/theme.py`, `app/i18n.py`, `app/ui_kit.py`, `app/widgets/wizard_widgets.py` (StepBar/WizardPanel/PreviewList/LogPanel), `app/engines/rename_engine.py`, `app/engines/rename_flow.py` (RenameJob), `app/engines/font_jp.py` (détection de polices JP trompeuses, voir ci-dessous), `app/wizards/rename_settings_panel.py`, `app/wizards/rename_macro.py` — tous extraits et branchés sur `ImageTool.py` via imports. 42 tests pytest passent.
- **Refonte visuelle globale — thème clair Acoustiguide** : `app/theme.py` migré du dark mode/orange vers un thème clair (fond blanc, accent rouge `#c9302c`), appliqué à **tout le tool** via les constantes globales (`BG`/`FG`/`ACCENT`/etc.), pas seulement HomeScreen ou GuideMark — voir Important decisions pour le détail (cette décision remplace celle de la ligne "Design GuideMark : dark mode..." ci-dessous, obsolète). `HomeScreen` refondu en grille de cards carrées (3 colonnes), fenêtre dimensionnée en fraction de l'écran (plus de taille fixe coupée). Correctifs ciblés : badges succès/erreur `LinkRedirectorWizard`, palette de coloration syntaxique `HtmlFormatterWizard` (VSCode Light+), bordures sur les swatches blancs de `ColorPickerDialog`.
- **DPI-awareness Windows** : le process se déclare `PROCESS_SYSTEM_DPI_AWARE` au démarrage (point d'entrée `ImageTool.py`) pour éviter le rendu flou que Windows applique par bitmap-scaling sur un écran >100%. Toutes les tailles de fenêtre fixes (`geometry`/`minsize` codées en dur dans les 14 wizards) passent maintenant par `scaled_geometry`/`scaled_minsize` (`app/ui_kit.py`), qui les multiplie par le facteur DPI réel de l'écran (`winfo_fpixels('1i') / 96`).
- **Langue japonaise retirée de l'UI** : le tool est un usage perso pour l'instant, pas encore partagé à l'équipe — le bouton de bascule 🇯🇵/🇬🇧 a été retiré de `HomeScreen`, la langue est forcée sur l'anglais au démarrage. Tout `app/i18n.py` (traductions FR/EN/JA) reste intact et inchangé, en dormance — à réactiver simplement en réintroduisant le bouton si l'outil est un jour partagé.
- **Détection de polices japonaises trompeuses** (`app/engines/font_jp.py`) : certaines polices (ex. variantes Adobe "trial/subset" comme Ryumin Std -KS/-KO) déclarent un cmap Unicode complet mais la majorité de leurs glyphes kanji sont en réalité des placeholders carrés identiques — invisible en inspectant seulement le cmap. `font_supports_japanese()` détecte ça en comparant les bounding box d'un échantillon de kana/kanji dessinés. Résultat mis en cache dans `_font_cache.json` (clé `jp_support`, calcul paresseux + scan en arrière-plan). Toggle "🇯🇵 JP fonts only" ajouté à `FontPreviewDialog`, utilisé par ImageGen et GuideMark pour choisir une police qui affiche vraiment le japonais dans les visuels générés.
- **Fix drop-shadow ImageGen** : le flou gaussien était appliqué sur les 4 canaux RGBA non-prémultipliés, ce qui mélangeait la couleur de l'ombre avec le noir du calque transparent sur les bords — produisant un liseret à deux teintes. Fix : flouter uniquement le canal alpha, puis reconstituer un calque RGB uniforme avec cet alpha flouté.
- **Design UI/UX GuideMark** (9/9 étapes) : nouvelle palette dark raffinée + échelle typographique stricte (`app/theme.py`), helpers `section_title/property_row/card/scrollbar` (`app/ui_kit.py`), migration vers `StepBar` partagé, cartes de template via `ui_kit.card()`, suppression de la double sélection de preset (Combobox redondant retiré, une seule surface = les cartes Step 0), scrollbars themées, `PREVIEW_BG` nommé. **Note** : la palette dark d'origine de ce chantier a depuis été remplacée par le thème clair global ci-dessus — les helpers structurels (StepBar, cartes, scrollbars) restent inchangés et valides.
- **Macro clic-droit "Renommer avec Image Tool"** (7/8 étapes du plan, étape 7 = test packagé restant) :
  - `RenameJob` (preview/execute) partagé entre RenameWizard classique et la macro — messages d'erreur améliorés (`PermissionError`/`FileExistsError` explicites), détection de collision de noms cibles.
  - `RenameSettingsPanel` factorisé — RenameWizard classique et macro utilisent le même panneau de réglages, zéro duplication.
  - `register_context_menu.py` : clé `HKCU\Software\Classes\*\shell\ImageToolRename` — testé réellement (clé créée, visible via `reg query`), pas de droits admin requis, réversible.
  - Aiguillage `--rename-macro` dans le point d'entrée — testé réellement en ligne de commande (lance `RenameMacroWizard` directement, sans `HomeScreen`), garde anti-régression validée (aucun argument / argument inattendu → HomeScreen normal).
  - Flow macro à 2 écrans (Settings → Preview/Confirmation, "Done" traité comme un état de l'écran 2) — testé de bout en bout sur fichiers jetables, renommage réel confirmé sur disque.
- **GuideMark — feuille mixte, numéros composés, export Illustrator** : moteur extrait dans `app/engines/guidemark_{model,layout,render,svg,paste}.py` + `vector_template.py` (le monolithe n'a plus que des wrappers) ; UI de la feuille dans `app/wizards/guidemark_sheet.py`.
  - **Feuille mixte** (étape 1) : liste de tuiles, chacune avec son propre design (sélecteur « Design pour les nouvelles tuiles » + « Appliquer ce design aux tuiles sélectionnées »), multi-sélection, ▲▼/✕/Ctrl+A.
  - **Numéros composés** (« 14-1 ») : éditeur de segments (taille, police, tracking, décalage vertical, espace avant), réglage groupé sur la sélection, décalage X/Y de l'étiquette, option « centrer seulement le numéro principal ». **Coller une liste** (presse-papiers/Excel, séparateurs `, ; 、` espaces, chiffres pleine chasse ; `14-1`/`14a` → grand numéro + suffixe à 40 % ou pt choisis). **Taille du numéro principal par tuile** (champ + « Appliquer à la sélection »). Champ « Preview n° » accepte `14-1`.
  - **Export SVG Illustrator** : un seul fichier (pages empilées) ou un par page ; en mode fichier unique, génère aussi un script `*_artboards.jsx` (Fichier > Scripts > Autre script… dans Illustrator) qui ouvre le SVG et crée un plan de travail par page (le SVG ne peut pas porter d'artboards) ; calques `Fond`/`Numeros`/`Reperes` ; **chaque étiquette = un seul `<text>`** (tspans + letter-spacing + baseline-shift, plus de positions par glyphe qu'Illustrator éclate en objets séparés) avec noms PostScript ; option contours (fonttools) ; **repères tombo** vectoriels (double L aux coins + croix centrales, fond perdu 3 mm, traits 10 mm) ; « Rogner la bordure du design » (0,8 mm, SVG seulement, quand des repères sont affichés).
  - **Templates** SVG/PDF/AI acceptés (SVG inliné en vrai vecteur ; PDF/AI rastérisés via pypdfium2 puis embarqués en image). Dépendances : `pypdfium2`, `resvg-py`.
  - **Tests** (100) : goldens pixel (`tests/golden/guidemark/`), feuille mixte/composés, collage, SVG (fidélité raster↔SVG via resvg, structure, un-seul-bloc, tombo, rognage), templates vectoriels.
  - **Limites connues** : feuille mixte non sauvegardée dans les presets ; même taille de tuile pour tous les designs d'une feuille ; kerning optique/chasse fixe seulement pour les étiquettes à un seul segment ; fond PDF/AI non vectoriel dans le SVG ; repères tombo de 13 mm demandent un espacement de tuiles ≥ 13 mm (≈ 26 mm pour qu'ils ne se touchent pas) ; comportement du texte dans Illustrator (tspans) non vérifié automatiquement.
  - **Anomalie héritée (non corrigée volontairement)** : le kerning « オプティカル » décale le texte de ~10 px quel que soit le DPI (marge de 10 px du canevas de mesure) → décalage physique dépendant du DPI. Le corriger changerait l'impression des presets existants ; à décider.
- Setup projet : `CLAUDE.md`/`PROJECT_STATUS.md` créés, `_lrw_config.json` (secret Cloudflare) ajouté au `.gitignore`.

## Next

- **Test bout-en-bout avec un vrai exe packagé** (étape 7 du plan macro) : créer un `.spec` PyInstaller (absent du repo), builder l'exe, repointer la clé de registre dessus, tester un clic-droit réel sur plusieurs fichiers d'un partage réseau UNC pour confirmer le comportement de `%*` en conditions réelles (validé uniquement en mode dev pour l'instant).
- Appliquer le design system GuideMark (palette + helpers `ui_kit`) à RenameWizard et ImageGenWizard.
- Chantier annoncé par l'utilisateur mais pas encore démarré : refonte plus profonde d'ImageGen (mise de côté volontairement pour l'instant).
- Chantier annoncé : gestion unifiée des templates/presets GuideMark (actuellement deux systèmes de stockage distincts — `_guidemark_templates.json` à 1 entrée basique vs `_guidemark_presets.json` à 5 entrées riches — pas encore harmonisés).

## Known issues

- Le clic-droit enregistré sur cette machine pointe vers `python.exe` + `ImageTool.py` du dev — **pas utilisable tel quel par un collègue** sans environnement Python. Nécessite le packaging (.exe) avant tout déploiement.
- `_lrw_config.json` (token Cloudflare LinkRedirector) en clair sur disque, hors git — à migrer vers un stockage sécurisé si ce wizard est un jour conservé.
- PDFExtractorWizard et HtmlFormatterWizard font un `pip install` silencieux au runtime — incompatible poste verrouillé, priorité basse (wizards occasionnels).
- Beaucoup de `except Exception: pass` dans le monolithe legacy — traités module par module à la migration, pas en un seul passage.
- `installer/` ne contient que l'ancien `.exe` compilé, pas de `.spec` à jour.
- Checkbox morte "process each subfolder independently" dans `RenameWizard` (Step Filter) — jamais lue, confirmée hors scope des chantiers actuels.
- `fonttools>=4.0` ajouté à `requirements.txt` (nécessaire pour `app/engines/font_jp.py`) — à installer dans un `.venv` recréé (`pip install -r requirements.txt`), pas encore inclus dans un éventuel build PyInstaller existant.

## Important decisions

- Refonte **progressive**, jamais de big-bang : `ImageTool.py` reste lançable à chaque étape.
- Périmètre v1 : Rename, GuideMark, ImageGen (usage quotidien) ; les 10 autres wizards restent dans le monolithe pour l'instant.
- **Thème** : décision inversée par rapport à la version précédente de ce document — passage du dark mode/orange vers un thème **clair** inspiré du site institutionnel Acoustiguide (fond blanc, accent rouge `#c9302c`), appliqué globalement via `app/theme.py`, pas seulement à GuideMark. Motivation : cohérence avec la nouvelle image de marque, lisibilité jugée meilleure par l'utilisateur.
- **Langue** : UI forcée en anglais, japonais retiré de l'accueil (voir Recently completed) — le tool est un usage perso pour l'instant ; à reconsidérer si l'outil est un jour partagé à l'équipe.
- Preset GuideMark : une seule surface de sélection (cartes visuelles Step 0).
- Macro Rename : intégration native Windows via `HKEY_CURRENT_USER` (pas HKLM, pas de droits admin requis) — choix fait pour un déploiement sur un parc d'entreprise sans garantie de droits admin par poste.
- Macro Rename : flow condensé à 2 écrans (Settings, Preview/Confirmation) — Folder et Filter du RenameWizard classique sautés car les fichiers arrivent déjà sélectionnés par l'Explorateur.
