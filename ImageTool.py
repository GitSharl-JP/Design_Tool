# =============================================================================
#  IMAGE TOOL  —  Rename / Resize / Compress
#  Python 3 + tkinter + Pillow
# =============================================================================

import os, re, sys, shutil, threading, struct, zlib, json, subprocess
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path

try:
    from PIL import Image
    PILLOW_OK = True
except ImportError:
    PILLOW_OK = False

DESKTOP = Path(os.path.expanduser("~")) / "Desktop"

# ── Windows drag-and-drop via WM_DROPFILES (no extra package needed) ─────────
def _enable_drop(widget, callback):
    """
    Register `widget` as a drop target on Windows.
    `callback(paths: list[str])` is called with the list of dropped paths.
    Falls back silently on non-Windows platforms.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes, ctypes.wintypes
        user32   = ctypes.windll.user32
        shell32  = ctypes.windll.shell32
        WM_DROPFILES = 0x0233
        GWL_WNDPROC  = -4

        # On 64-bit Windows all pointer-sized returns MUST use c_ssize_t,
        # otherwise ctypes truncates to 32 bits → corrupted function pointer → crash.
        WNDPROCTYPE = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.wintypes.HWND,
            ctypes.c_uint,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        )
        user32.SetWindowLongPtrW.restype  = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user32.CallWindowProcW.restype    = ctypes.c_ssize_t
        user32.GetParent.restype          = ctypes.wintypes.HWND

        _orig_proc = [ctypes.c_ssize_t(0)]
        _proc_ref  = [None]   # keep alive to prevent GC

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_DROPFILES:
                try:
                    count = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
                    paths = []
                    for i in range(count):
                        buf = ctypes.create_unicode_buffer(260)
                        shell32.DragQueryFileW(wparam, i, buf, 260)
                        paths.append(buf.value)
                    shell32.DragFinish(wparam)
                    widget.after(0, lambda: callback(paths))
                except Exception:
                    pass
                return 0
            return user32.CallWindowProcW(
                _orig_proc[0], hwnd, msg, wparam, lparam)

        def _attach():
            try:
                hwnd = widget.winfo_toplevel().winfo_id()
                parent = user32.GetParent(hwnd)
                if parent:
                    hwnd = parent
                shell32.DragAcceptFiles(hwnd, True)
                new_proc      = WNDPROCTYPE(_wnd_proc)
                _proc_ref[0]  = new_proc
                old           = user32.SetWindowLongPtrW(hwnd, GWL_WNDPROC,
                                    ctypes.cast(new_proc, ctypes.c_ssize_t))
                _orig_proc[0] = ctypes.c_ssize_t(old)
            except Exception:
                pass   # drag-drop unavailable — not fatal

        widget.after(200, _attach)
    except Exception:
        pass

# ── Colours & fonts ──────────────────────────────────────────────────────────
BG           = "#0f0f1c"   # deep navy-black
BG2          = "#181828"   # card / panel
BG3          = "#22223a"   # input / sink
ACCENT       = "#7c6af7"   # primary purple
ACCENT2      = "#5a4fcf"   # pressed
ACCENT_LIGHT = "#a89af9"   # hover highlight
FG           = "#eeeeff"
FG2          = "#7878a8"
FG_GREEN     = "#6fcf97"
FG_RED       = "#eb5757"
FG_YELLOW    = "#f2c94c"
RADIUS       = 8

# ── Internationalisation ──────────────────────────────────────────────────────
_LANG = "en"   # "en" or "ja"

_STRINGS: dict = {
    "en": {
        # ImageGenWizard
        "igw.title":      "Image Tool  —  Image Creator",
        "igw.hdr":        "🎨  Image Creator",
        "igw.steps":      ["1 · Canvas", "2 · Background", "3 · Numbers",
                           "4 · Corners", "5 · Output", "6 · Generate"],
        "igw.back":       "← Back",
        "igw.next":       "Next →",
        "igw.restart":    "↩  Start over",
        "igw.prev_title": "Live Preview",
        "igw.prev_ph":    "Preview…",
        "igw.refresh":    "🔄  Refresh",
        # Step 0 — Canvas
        "s0.title":       "Canvas size",
        "s0.width":       "Width :",
        "s0.height_px":   "px     Height :",
        "s0.link_wh":     "🔗  Link W = H  (auto square)",
        "s0.presets":     "Preset sizes :",
        "s0.proj":        "Projects :",
        "s0.std":         "Standard :",
        "s0.analyze":     "🔍  Analyse reference image…",
        "s0.analyze_hint": "Extract position, colour and font size from an existing numbered image.",
        "anal.title":     "Extracted settings",
        "anal.dlg":       "Select reference image",
        "anal.none":      "Could not detect any text in this image. Try with a cleaner image.",
        # Step 1 — Background
        "s1.title":       "Image background",
        "s1.color":       "Solid colour",
        "s1.hex":         "Hex code :",
        "s1.quick":       "Quick :",
        "s1.image":       "Background image  (JPG / PNG)",
        "s1.browse":      "📂  Browse…",
        "s1.no_file":     "  (no file selected)",
        "s1.hint":        "The image will be resized to fit the canvas exactly.",
        "s1.bg_dlg":      "Select background image",
        "s1.pattern":     "Image pattern  (multiple images, cycling)",
        "s1.pat_add":     "📂  Add image…",
        "s1.pat_hint":    "Images cycle in order  ·  #1→img1, #2→img2, #3→img1…",
        "s1.pat_empty":   "(drag & drop images here, or click Add)",
        # Step 2 — Numbers
        "s2.style_title": "Number style",
        "s2.nums_title":  "Numbers to generate",
        "s2.nums_hint":   "Examples:  1-30   |   1-30, 55, 60, 100, 200   |   5, 10, 15, 20",
        "s2.count":       "→ {n} image(s)",
        "s2.count_range": "  (#{a}…#{b})",
        "s2.font_title":  "Font",
        "s2.family":      "Family :",
        "s2.style":       "Style    :",
        "s2.size":        "  Size :",
        "s2.tracking":    "  Tracking :",
        "s2.tracking_hint": "Letter spacing  (0 = default, negative = tighter, positive = looser)",
        "s2.px":          "px",
        "s2.preview_btn": "🔍  Preview fonts",
        "s2.mode_lbl":    "Size mode :",
        "s2.fixed":       "Fixed",
        "s2.auto":        "Auto-adapt  (3+ digits)",
        "s2.auto_hint":   "➔ Auto: font shrinks if the number exceeds canvas bounds (100, 200…)",
        "s2.tip":         "Tip: type in Family to filter  ·  or click \u201cPreview fonts\u201d to browse designs.",
        "s2.color_title": "Text colour",
        "s2.hex_lbl":     "Hex :",
        "s2.quick_lbl":   "Quick :",
        "s2.shadow_title":"Drop shadow",
        "s2.shadow_on":   "Enable shadow",
        "s2.shadow_col":  "Colour :",
        "s2.shadow_x":    "Offset X :",
        "s2.shadow_y":    "px   Y :",
        "s2.shadow_blur": "Blur :",
        "s2.pos_title":   "Number position",
        "s2.pos_manual":  "Manual position  (disable auto-centre)",
        "s2.pos_hint":    "Top-left anchor of the text (0,0 = top-left of canvas) :",
        "s2.pos_x":       "X :",
        "s2.pos_xy":      "px   Y :",
        "s2.pos_px":      "px",
        # Step 3 — Corners
        "s3.title":       "Corner shape",
        "s3.sharp":       "Sharp corners  (clean rectangle)",
        "s3.rounded":     "Rounded corners",
        "s3.radius":      "Radius :",
        "s3.warn_jpeg":   "⚠  JPEG with rounded corners: transparent areas will be filled with white.",
        "s3.hint_png":    "    PNG format: full transparency — ideal for rounded corners.",
        # Step 4 — Output
        "s4.title":       "Output format",
        "s4.quality":     "Quality :",
        "s4.naming":      "File naming",
        "s4.prefix":      "Prefix :",
        "s4.folder":      "Output folder",
        "s4.choose":      "📂  Choose…",
        "s4.dlg_title":   "Output folder",
        # Step 5 — Generate
        "s5.summary":     "Summary",
        "s5.generate":    "▶  Generate images",
        "s5.refresh":     "🔄  Refresh preview",
        "s5.sum_canvas":  "Canvas   : {w} × {h} px",
        "s5.sum_nums":    "Numbers  : {nums}  ({n} image(s))",
        "s5.sum_font":    "Font     : {fam} {sty}  {sz} px{auto}",
        "s5.sum_bg":      "Bg       : {bg}",
        "s5.sum_corners": "Corners  : {c}",
        "s5.sum_fmt":     "Format   : {fmt}    Prefix : {pfx}    Suffix : {sfx}",
        "s5.sum_out":     "Output   : {out}",
        "s5.auto_tag":    "  (auto size)",
        "s5.sharp":       "Sharp",
        "s5.rounded_r":   "Rounded r={r}px",
        # Errors / messages
        "err.empty_list": "Empty list",
        "err.empty_msg":  "Please enter at least one number or range.",
        "err.bad_value":  "Invalid value",
        "err.no_bg":      "Missing background image",
        "err.no_bg_msg":  "Please select a background image.",
        "err.no_pat":     "No pattern images",
        "err.no_pat_msg": "Add at least one image to the pattern.",
        "err.done":       "Done ✔",
        "err.done_msg":   "{ok} image(s) generated in:\n{out}",
        "err.log_sum":    "── {ok}/{total} images generated → {out}",
        # FontPreviewDialog
        "fpd.title":      "Choose a font",
        "fpd.search":     "🔍  Search :",
        "fpd.count":      "{n} font(s)",
        "fpd.cancel":     "✕  Cancel",
        # HomeScreen
        "home.sub":       "Professional image workflow suite  ·  v2.0",
        "home.pills":     ["7 tools", "Batch", "Live preview", "100% local"],
        "home.tools":     "TOOLS",
        "home.adv":       "ADVANCED",
        "home.pipeline":  "▶  Pipeline",
        "home.pipe_desc": "  Chain multiple tools  (Resize → Compress → Rename)",
        "home.rename_d":  "Rename files — digits, counters, custom name maps",
        "home.resize_d":  "Resize images — width, height, box fit, percentage",
        "home.comp_d":    "Reduce file size — fixed quality or target KB",
        "home.gen_d":     "Create numbered images — background, font, rounded corners",
        "home.clip_d":    "Favourite snippets — copy in one click, by category",
        "home.lang_btn":  "🇯🇵 日本語",
        # ColorPickerDialog
        "cpd.title":      "Choose a colour",
        "cpd.cancel":     "Cancel",
        # New features
        "s4.suffix":      "Suffix :",
        "s5.open_folder": "📂  Open folder",
        "s5.grid_title":  "Sample grid",
        "s1.paste_path":  "📋  Paste path",
        "s2.guide_title": "Guide mark",
        "s2.guide_on":    "Show guide  (safe zone)",
        "s2.guide_margin":"Margin :",
        "s2.guide_opus":  "OPUS 40px",
        "s2.guide_hint":  "→ When active, number stays within the guide zone (also constrains manual position).",
        # Presets
        "preset.title":   "Presets",
        "preset.save":    "💾  Save preset",
        "preset.load":    "📂  Load preset",
        "preset.delete":  "🗑  Delete",
        "preset.name_dlg":"Preset name :",
        "preset.saved":   "Preset saved : {name}",
        "preset.loaded":  "Preset loaded : {name}",
        "preset.none":    "(no presets yet)",
        "preset.confirm_del": "Delete preset '{name}'?",
        # Undo generation
        "undo.btn":       "↩  Undo last generation",
        "undo.confirm":   "Delete {n} generated file(s)?",
        "undo.done":      "{n} file(s) deleted.",
        "undo.empty":     "Nothing to undo.",
        # Batch convert
        "conv.title":     "Batch Convert",
        "conv.desc":      "Convert images between formats — JPG, PNG, WebP, BMP",
        "conv.src_fmt":   "Source format :",
        "conv.dst_fmt":   "Target format :",
        "conv.quality":   "JPEG quality :",
        "conv.go":        "▶  Convert",
        "conv.done":      "{ok}/{total} file(s) converted.",
        "conv.overwrite": "Overwrite originals",
        "conv.out_sub":   "Save in sub-folder : converted/",
        # GuideMarkWizard
        "gmw.title":      "Guide Mark Generator",
        "gmw.desc":       "Arrange numbered images for print — A3ノビ, A3, A4",
        "gmw.nums":       "Number range :",
        "gmw.folder":     "Source folder :",
        "gmw.prefix":     "Prefix :",
        "gmw.suffix":     "Suffix :",
        "gmw.ext":        "Extension :",
        "gmw.paper":      "Paper size :",
        "gmw.orient":     "Orientation :",
        "gmw.portrait":   "Portrait",
        "gmw.landscape":  "Landscape",
        "gmw.tile_w":     "Tile width (mm) :",
        "gmw.tile_h":     "Tile height (mm) :",
        "gmw.margin":     "Page margin (mm) :",
        "gmw.gap":        "Spacing (mm) :",
        "gmw.dpi":        "Resolution (DPI) :",
        "gmw.out_folder": "Output folder :",
        "gmw.printers":   "Printer :",
        "gmw.preview":    "Layout preview",
        "gmw.export_png": "💾  Export PNG",
        "gmw.export_pdf": "📄  Export PDF  (.ai compatible)",
        "gmw.print":      "🖨  Print…",
        "gmw.layout_sum": "{placed}/{total} tiles placed  ·  grid {cols}×{rows}",
        "gmw.no_src":     "Please select a source folder first.",
        "gmw.done_png":   "PNG exported : {path}",
        "gmw.done_pdf":   "PDF exported : {path}",
        "gmw.src_mode":     "Source :",
        "gmw.src_folder":   "📁  From folder",
        "gmw.src_render":   "🎨  Generate tiles",
        "gmw.design":       "Design :",
        "gmw.des_num":      "Number only",
        "gmw.des_num_text": "Number + sub-text",
        "gmw.tile_bg":      "Tile background :",
        "gmw.overlay":      "Overlay image :",
        "gmw.num_font":     "Number font :",
        "gmw.num_fsize":    "Number size (pt) :",
        "gmw.num_auto":     "Auto-scale to tile",
        "gmw.num_color":    "Number colour :",
        "gmw.sub_font":     "Sub-text font :",
        "gmw.sub_fsize":    "Sub-text size (pt) :",
        "gmw.sub_tracking": "Tracking (px) :",
        "gmw.sub_color":    "Sub-text colour :",
        "gmw.design_preset":  "Design :",
        "gmw.show_subtext":   "Show sub-text",
        "gmw.monochrome":     "Monochrome (K only)",
        "gmw.tile_size":      "Tile size :",
        "gmw.sub_text":       "Sub-text :",
        "gmw.step_design":    "Design",
        "gmw.step_configure": "Configure",
        "gmw.step_print":     "Print",
        "gmw.pick_design":    "Choose a design",
        "gmw.back":           "Back",
        "gmw.next":           "Next",
        "gmw.num_range":      "Numbers",
        "gmw.advanced":       "Advanced",
        "gmw.tile_preview_num": "Preview n\u00b0 :",
        "home.gmw_d":     "Arrange numbered images for print — auto-layout, PDF/PNG export",
    },
    "ja": {
        # ImageGenWizard
        "igw.title":      "Image Tool  —  画像クリエーター",
        "igw.hdr":        "🎨  画像クリエーター",
        "igw.steps":      ["1 · キャンバス", "2 · 背景", "3 · 番号",
                           "4 · 角", "5 · 出力", "6 · 生成"],
        "igw.back":       "← 戻る",
        "igw.next":       "次へ →",
        "igw.restart":    "↩  最初から",
        "igw.prev_title": "ライブプレビュー",
        "igw.prev_ph":    "プレビュー…",
        "igw.refresh":    "🔄  更新",
        # Step 0 — Canvas
        "s0.title":       "キャンバスサイズ",
        "s0.width":       "幅 :",
        "s0.height_px":   "px     高さ :",
        "s0.link_wh":     "🔗  W = H リンク（自動正方形）",
        "s0.presets":     "プリセット :",
        "s0.proj":        "プロジェクト :",
        "s0.std":         "標準 :",
        "s0.analyze":     "🔍  参考画像を解析…",
        "s0.analyze_hint": "既存の番号画像から位置・色・フォントサイズを抜き出します。",
        "anal.title":     "設定を抽出しました",
        "anal.dlg":       "参考画像を選択",
        "anal.none":      "画像からテキストを検出できませんでした。",
        # Step 1 — Background
        "s1.title":       "画像の背景",
        "s1.color":       "単色",
        "s1.hex":         "Hex コード :",
        "s1.quick":       "クイック :",
        "s1.image":       "背景画像  (JPG / PNG)",
        "s1.browse":      "📂  参照…",
        "s1.no_file":     "  （ファイル未選択）",
        "s1.hint":        "画像はキャンバスサイズに合わせてリサイズされます。",
        "s1.bg_dlg":      "背景画像を選択",
        "s1.pattern":     "画像パターン（複数画像のサイクル）",
        "s1.pat_add":     "📂  画像を追加…",
        "s1.pat_hint":    "順番にサイクル  ·  #1→img1, #2→img2, #3→img1…",
        "s1.pat_empty":   "（ここに画像をドラッグするか追加してください）",
        # Step 2 — Numbers
        "s2.style_title": "番号スタイル",
        "s2.nums_title":  "生成する番号",
        "s2.nums_hint":   "例：  1-30   |   1-30, 55, 60, 100, 200   |   5, 10, 15, 20",
        "s2.count":       "→ {n} 枚",
        "s2.count_range": "  (#{a}…#{b})",
        "s2.font_title":  "フォント",
        "s2.family":      "ファミリー :",
        "s2.style":       "スタイル  :",
        "s2.size":        "  サイズ :",
        "s2.tracking":    "  トラッキング :",
        "s2.tracking_hint": "文字間距（0=デフォルト，負=素·正=適）",
        "s2.px":          "px",
        "s2.preview_btn": "🔍  フォントプレビュー",
        "s2.mode_lbl":    "サイズモード :",
        "s2.fixed":       "固定",
        "s2.auto":        "自動調整  (3桁以上)",
        "s2.auto_hint":   "➔ 自動：番号がはみ出す場合にフォントサイズを縮小 (100, 200…)",
        "s2.tip":         "ヒント：ファミリー欄に入力してフィルタ  ·  または\u300cフォントプレビュー\u300dでデザイン確認。",
        "s2.color_title": "文字色",
        "s2.hex_lbl":     "Hex :",
        "s2.quick_lbl":   "クイック :",
        "s2.shadow_title":"ドロップシャドウ",
        "s2.shadow_on":   "シャドウを有効にする",
        "s2.shadow_col":  "色 :",
        "s2.shadow_x":    "X オフセット :",
        "s2.shadow_y":    "px   Y :",
        "s2.shadow_blur": "ぼかし :",
        "s2.pos_title":   "番号の位置",
        "s2.pos_manual":  "手動位置  （自動センタリングを無効化）",
        "s2.pos_hint":    "テキストの左上アンカー（0,0 = キャンバス左上）:",
        "s2.pos_x":       "X :",
        "s2.pos_xy":      "px   Y :",
        "s2.pos_px":      "px",
        # Step 3 — Corners
        "s3.title":       "角のスタイル",
        "s3.sharp":       "直角  （クリアな長方形）",
        "s3.rounded":     "角丸",
        "s3.radius":      "半径 :",
        "s3.warn_jpeg":   "⚠  角丸 + JPEG：透明部分は白で塗りつぶされます。",
        "s3.hint_png":    "    PNG 形式：完全透明 — 角丸に最適。",
        # Step 4 — Output
        "s4.title":       "出力フォーマット",
        "s4.quality":     "品質 :",
        "s4.naming":      "ファイル名設定",
        "s4.prefix":      "プレフィックス :",
        "s4.folder":      "出力フォルダ",
        "s4.choose":      "📂  選択…",
        "s4.dlg_title":   "出力フォルダ",
        # Step 5 — Generate
        "s5.summary":     "サマリー",
        "s5.generate":    "▶  画像を生成",
        "s5.refresh":     "🔄  プレビュー更新",
        "s5.sum_canvas":  "キャンバス : {w} × {h} px",
        "s5.sum_nums":    "番号      : {nums}  ({n} 枚)",
        "s5.sum_font":    "フォント  : {fam} {sty}  {sz} px{auto}",
        "s5.sum_bg":      "背景      : {bg}",
        "s5.sum_corners": "角        : {c}",
        "s5.sum_fmt":     "形式      : {fmt}    プレフィックス : {pfx}    サフィックス : {sfx}",
        "s5.sum_out":     "出力先    : {out}",
        "s5.auto_tag":    "  (自動サイズ)",
        "s5.sharp":       "直角",
        "s5.rounded_r":   "角丸 r={r}px",
        # Errors / messages
        "err.empty_list": "リストが空です",
        "err.empty_msg":  "番号または範囲を1つ以上入力してください。",
        "err.bad_value":  "無効な値",
        "err.no_bg":      "背景画像がありません",
        "err.no_bg_msg":  "背景画像を選択してください。",
        "err.no_pat":     "パターン画像がありません",
        "err.no_pat_msg": "パターンに画像を少なくとも1枚追加してください。",
        "err.done":       "完了 ✔",
        "err.done_msg":   "{ok} 枚の画像を生成しました：\n{out}",
        "err.log_sum":    "── {ok}/{total} 枚生成完了 → {out}",
        # FontPreviewDialog
        "fpd.title":      "フォントを選択",
        "fpd.search":     "🔍  検索 :",
        "fpd.count":      "{n} フォント",
        "fpd.cancel":     "✕  キャンセル",
        # HomeScreen
        "home.sub":       "プロ向け画像ワークフロースイート  ·  v2.0",
        "home.pills":     ["7 ツール", "バッチ", "ライブプレビュー", "100% ローカル"],
        "home.tools":     "ツール",
        "home.adv":       "上級",
        "home.pipeline":  "▶  パイプライン",
        "home.pipe_desc": "  ツールを連結  (Resize → Compress → Rename)",
        "home.rename_d":  "ファイルのリネーム — 番号・カウンター・カスタム名",
        "home.resize_d":  "画像リサイズ — 幅・高さ・ボックスフィット・%",
        "home.comp_d":    "ファイルサイズ削減 — 品質固定または KB 目標",
        "home.gen_d":     "番号付き画像作成 — 背景・フォント・角丸",
        "home.clip_d":    "スニペット管理 — ワンクリックコピー・カテゴリ別",
        "home.lang_btn":  "🇬🇧 English",
        # ColorPickerDialog
        "cpd.title":      "色を選択",
        "cpd.cancel":     "キャンセル",
        # New features
        "s4.suffix":      "サフィックス :",
        "s5.open_folder": "📂  フォルダを開く",
        "s5.grid_title":  "サンプルグリッド",
        "s1.paste_path":  "📋  パスを貼り付け",
        "s2.guide_title": "ガイドマーク",
        "s2.guide_on":    "ガイドを表示  (セーフゾーン)",
        "s2.guide_margin":"マージン :",
        "s2.guide_opus":  "OPUS 40px",
        "s2.guide_hint":  "→ 有効時、番号はガイドゾーン内に制限されます（手動位置も制限）。",
        # Presets
        "preset.title":   "プリセット",
        "preset.save":    "💾  プリセット保存",
        "preset.load":    "📂  プリセット読込",
        "preset.delete":  "🗑  削除",
        "preset.name_dlg":"プリセット名 :",
        "preset.saved":   "保存しました : {name}",
        "preset.loaded":  "読み込みました : {name}",
        "preset.none":    "（プリセットなし）",
        "preset.confirm_del": "プリセット '{name}' を削除しますか？",
        # Undo generation
        "undo.btn":       "↩  生成を取り消す",
        "undo.confirm":   "{n} ファイルを削除しますか？",
        "undo.done":      "{n} ファイルを削除しました。",
        "undo.empty":     "取り消すものがありません。",
        # Batch convert
        "conv.title":     "一括変換",
        "conv.desc":      "画像フォーマットを変換 — JPG, PNG, WebP, BMP",
        "conv.src_fmt":   "変換元 :",
        "conv.dst_fmt":   "変換先 :",
        "conv.quality":   "JPEG品質 :",
        "conv.go":        "▶  変換",
        "conv.done":      "{ok}/{total} ファイル変換完了。",
        "conv.overwrite": "元ファイルを上書き",
        "conv.out_sub":   "サブフォルダに保存 : converted/",
        # GuideMarkWizard
        "gmw.title":      "ガイドマーク生成",
        "gmw.desc":       "番号画像を印刷用に自動配置 — A3ノビ, A3, A4",
        "gmw.nums":       "番号範囲 :",
        "gmw.folder":     "ソースフォルダ :",
        "gmw.prefix":     "プレフィックス :",
        "gmw.suffix":     "サフィックス :",
        "gmw.ext":        "拡張子 :",
        "gmw.paper":      "用紙サイズ :",
        "gmw.orient":     "向き :",
        "gmw.portrait":   "縦",
        "gmw.landscape":  "横",
        "gmw.tile_w":     "タイル幅 (mm) :",
        "gmw.tile_h":     "タイル高さ (mm) :",
        "gmw.margin":     "ページ余白 (mm) :",
        "gmw.gap":        "間隔 (mm) :",
        "gmw.dpi":        "解像度 (DPI) :",
        "gmw.out_folder": "出力フォルダ :",
        "gmw.printers":   "プリンター :",
        "gmw.preview":    "レイアウトプレビュー",
        "gmw.export_png": "💾  PNG エクスポート",
        "gmw.export_pdf": "📄  PDF エクスポート  (.ai 互換)",
        "gmw.print":      "🖨  印刷…",
        "gmw.layout_sum": "{placed}/{total} タイル配置  ·  グリッド {cols}×{rows}",
        "gmw.no_src":     "先にソースフォルダを選択してください。",
        "gmw.done_png":   "PNG エクスポート完了 : {path}",
        "gmw.done_pdf":   "PDF エクスポート完了 : {path}",
        "gmw.src_mode":     "ソース :",
        "gmw.src_folder":   "📁  フォルダから",
        "gmw.src_render":   "🎨  タイルを生成",
        "gmw.design":       "デザイン :",
        "gmw.des_num":      "数字のみ",
        "gmw.des_num_text": "数字 + テキスト",
        "gmw.tile_bg":      "タイル背景 :",
        "gmw.overlay":      "オーバーレイ画像 :",
        "gmw.num_font":     "数字フォント :",
        "gmw.num_fsize":    "数字サイズ (pt) :",
        "gmw.num_auto":     "タイルに合わせて自動調整",
        "gmw.num_color":    "数字の色 :",
        "gmw.sub_font":     "サブテキストフォント :",
        "gmw.sub_fsize":    "サブテキストサイズ (pt) :",
        "gmw.sub_tracking": "トラッキング (px) :",
        "gmw.sub_color":    "サブテキストの色 :",
        "gmw.design_preset":  "デザイン :",
        "gmw.show_subtext":   "サブテキストを表示",
        "gmw.monochrome":     "モノクロ (K のみ)",
        "gmw.tile_size":      "タイルサイズ :",
        "gmw.sub_text":       "サブテキスト :",
        "gmw.step_design":    "デザイン",
        "gmw.step_configure": "設定",
        "gmw.step_print":     "印刷",
        "gmw.pick_design":    "デザインを選択してください",
        "gmw.back":           "戻る",
        "gmw.next":           "次へ",
        "gmw.num_range":      "番号",
        "gmw.advanced":       "詳細設定",
        "gmw.tile_preview_num": "プレビュー番号 :",
        "home.gmw_d":     "番号画像を印刷用に自動配置 — PDF/PNG エクスポート",
    },
}


def _t(key: str, **kw) -> str:
    """Return translated string for the current language (_LANG)."""
    en  = _STRINGS["en"]
    lo  = _STRINGS.get(_LANG, en)
    val = lo.get(key, en.get(key, key))
    if kw and isinstance(val, str):
        try:
            return val.format(**kw)
        except (KeyError, IndexError):
            return val
    return val


_FONT_FACE_EN = "Segoe UI"
_FONT_FACE_JA = "Noto Sans Mono JP"

def _ui_font():
    """Return the UI font face for the active language."""
    return _FONT_FACE_JA if _LANG == "ja" else _FONT_FACE_EN

def _update_font_constants():
    """Rebuild FONT_* globals after a language change."""
    global FONT_H1, FONT_H2, FONT_BODY, FONT_SM, FONT_MONO
    face = _ui_font()
    FONT_H1   = (face, 18, "bold")
    FONT_H2   = (face, 13, "bold")
    FONT_BODY = (face, 10)
    FONT_SM   = (face, 9)
    FONT_MONO = ("Consolas", 9)   # keep monospace for code snippets

_update_font_constants()  # initialise with default language

# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_size(b):
    if b >= 1_048_576: return f"{b/1_048_576:.2f} MB"
    return f"{b/1024:.1f} KB"

def collect_files(folder, exts, recurse):
    folder = Path(folder)
    files  = []
    globs  = exts if exts else ["*.*"]
    for g in globs:
        if recurse:
            files += sorted(folder.rglob(g))
        else:
            files += sorted(folder.glob(g))
    seen = set(); unique = []
    for f in files:
        if f in seen or not f.is_file(): continue
        seen.add(f); unique.append(f)
    return sorted(unique, key=lambda p: str(p).lower())

def first_digits(name):
    m = re.search(r'\d+', name)
    return m.group() if m else None

# ── Rename engine ─────────────────────────────────────────────────────────────
def compute_new_name(path: Path, cfg: dict, counter_ref: list) -> str:
    base = path.stem
    ext  = path.suffix

    mode = cfg.get("num_mode", "1")
    if mode == "2":
        d = first_digits(base)
        base = d if d else base
    elif mode == "3":
        w = cfg.get("pad_width", 3)
        base = str(counter_ref[0]).zfill(w)
        counter_ref[0] += 1
    elif mode == "4":
        d = first_digits(base)
        if d:
            idx = int(d) - cfg.get("map_start", 1)
            nm  = cfg.get("name_map", [])
            base = nm[idx] if 0 <= idx < len(nm) else base
        # keep base as-is if no digit

    find = cfg.get("find_text", "")
    repl = cfg.get("replace_text", "")
    if find:
        base = base.replace(find, repl)

    return cfg.get("prefix","") + base + cfg.get("suffix","") + ext

# ── Compress engine ───────────────────────────────────────────────────────────
def compress_jpeg_pillow(src: Path, dst: Path, quality: int):
    img = Image.open(src)
    if img.mode in ("RGBA","P","LA"):
        bg = Image.new("RGB", img.size, (255,255,255))
        bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA","LA") else None)
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.save(dst, "JPEG", quality=quality, optimize=True, progressive=True)

def find_quality_for_target(src: Path, target_kb: int, min_q: int) -> tuple[int,int]:
    import io
    target_bytes = target_kb * 1024
    img = Image.open(src)
    if img.mode in ("RGBA","P","LA"):
        bg = Image.new("RGB", img.size, (255,255,255))
        bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA","LA") else None)
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    lo, hi, best_q, best_size = min_q, 95, -1, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=mid, optimize=True)
        size = buf.tell()
        if size <= target_bytes:
            best_q, best_size, lo = mid, size, mid + 1
        else:
            hi = mid - 1
    if best_q == -1:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=min_q, optimize=True)
        best_q, best_size = min_q, buf.tell()
    return best_q, best_size

# ── Resize engine ─────────────────────────────────────────────────────────────
def compute_new_size(w, h, cfg):
    mode = cfg.get("resize_mode","1")
    tw   = cfg.get("target_w", 0)
    th   = cfg.get("target_h", 0)
    pct  = cfg.get("pct", 100)
    if mode == "1":
        r = tw / w;  return int(tw), int(h * r)
    elif mode == "2":
        r = th / h;  return int(w * r), int(th)
    elif mode == "3":
        r = min(tw/w, th/h); return int(w*r), int(h*r)
    elif mode == "4":
        return tw, th
    else:
        r = pct / 100.0; return int(w*r), int(h*r)

def do_resize(src: Path, dst: Path, cfg: dict):
    img  = Image.open(src)
    nw, nh = compute_new_size(img.width, img.height, cfg)
    img  = img.resize((nw, nh), Image.LANCZOS)
    q    = cfg.get("jpeg_quality", 90)
    ext  = src.suffix.lower()
    if ext in (".jpg",".jpeg"):
        img.save(dst, "JPEG", quality=q, optimize=True)
    else:
        img.save(dst)

# =============================================================================
#  GUI helpers
# =============================================================================
def styled_frame(parent, bg=None, **kw):
    return tk.Frame(parent, bg=bg or BG, **kw)

def label(parent, text, font=FONT_BODY, fg=FG, bg=None, **kw):
    return tk.Label(parent, text=text, font=font, fg=fg, bg=bg or BG, **kw)

def btn(parent, text, cmd, primary=True, **kw):
    bg    = ACCENT if primary else BG3
    hover = ACCENT2 if primary else "#2e2e50"
    b = tk.Button(parent, text=text, command=cmd,
                  bg=bg, fg=FG, font=FONT_BODY,
                  relief="flat", bd=0, padx=14, pady=7,
                  activebackground=hover, activeforeground=FG,
                  cursor="hand2", **kw)
    b.bind("<Enter>", lambda e, _b=b, _h=hover: _b.config(bg=_h))
    b.bind("<Leave>", lambda e, _b=b, _bg=bg:   _b.config(bg=_bg))
    return b

def entry(parent, textvariable=None, width=30, **kw):
    return tk.Entry(parent, textvariable=textvariable, width=width,
                    bg=BG3, fg=FG, insertbackground=FG,
                    relief="flat", font=FONT_BODY, bd=4, **kw)

def separator(parent):
    return tk.Frame(parent, bg=BG3, height=1)


def _enable_mousewheel_scroll(canvas: tk.Canvas):
    """Make mouse-wheel scroll 'canvas' regardless of which child widget the
    pointer is over.  Call once after the canvas + its children exist."""
    def _scroll(event):
        # Linux sends Button-4/5, Windows sends <MouseWheel> with delta
        delta = -1 if event.num == 5 else (1 if event.num == 4
                else -1 * (event.delta // 120))
        canvas.yview_scroll(delta, "units")

    def _bind_tree(widget):
        widget.bind("<MouseWheel>",  _scroll, add="+")
        widget.bind("<Button-4>",    _scroll, add="+")
        widget.bind("<Button-5>",    _scroll, add="+")
        for child in widget.winfo_children():
            _bind_tree(child)

    _bind_tree(canvas)
    # Also watch for new children added later (e.g. FontPreview rows)
    canvas._mw_bind_tree = _bind_tree   # expose so callers can re-run after rebuild

def combo(parent, var, values, width=20):
    cb = ttk.Combobox(parent, textvariable=var, values=values,
                      width=width, state="readonly", font=FONT_BODY)
    cb.configure(style="Custom.TCombobox")
    return cb

def make_scrolled_list(parent, height=14):
    frame = styled_frame(parent)
    sb    = tk.Scrollbar(frame, bg=BG2, troughcolor=BG, width=10)
    lb    = tk.Listbox(frame, bg=BG2, fg=FG, font=FONT_MONO,
                       selectbackground=ACCENT, selectforeground=FG,
                       height=height, relief="flat", bd=0,
                       yscrollcommand=sb.set, activestyle="none")
    sb.config(command=lb.yview)
    lb.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    return frame, lb

# =============================================================================
#  Folder browser widget (reusable)
# =============================================================================
class FolderBrowser(tk.Frame):
    def __init__(self, parent, on_select=None, next_cb=None, **kw):
        """on_select(path) called when folder chosen. next_cb() optional — called after select."""
        super().__init__(parent, bg=BG, **kw)
        self.on_select   = on_select
        self.next_cb     = next_cb
        self._history    = []
        self._current    = DESKTOP
        self._build()
        self._refresh()

    def _build(self):
        top = styled_frame(self)
        top.pack(fill="x", pady=(0,6))
        self._path_var = tk.StringVar()
        label(top, "📂", font=("Segoe UI",11), bg=BG).pack(side="left")
        self._path_lbl = label(top, "", font=FONT_SM, fg=FG2, bg=BG)
        self._path_lbl.pack(side="left", padx=4)
        self._back_btn = btn(top, "← Back", self._go_back, primary=False)
        self._back_btn.pack(side="right")

        frm, self._lb = make_scrolled_list(self, height=10)
        frm.pack(fill="both", expand=True)
        self._lb.bind("<Double-Button-1>", self._enter_folder)

        bot = styled_frame(self)
        bot.pack(fill="x", pady=(6,0))
        btn(bot, "✔  Select this folder", self._select, primary=True).pack(side="left")
        btn(bot, "Type path…", self._custom_path, primary=False).pack(side="left", padx=6)

        # Windows drag-and-drop — dropping a folder navigates to it
        def _on_drop(paths):
            p = Path(paths[0])
            target = p if p.is_dir() else p.parent
            self._current = target
            self._refresh()
        _enable_drop(self._lb, _on_drop)

    def _refresh(self):
        self._path_lbl.config(text=str(self._current))
        self._back_btn.config(state="normal" if self._history else "disabled")
        self._lb.delete(0, "end")
        try:
            entries = sorted(self._current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            subs  = [d for d in entries if d.is_dir()  and not d.name.startswith(".")]
            files = [f for f in entries if f.is_file() and not f.name.startswith(".")]
        except PermissionError:
            subs = []; files = []
        for d in subs:
            self._lb.insert("end", "  📁  " + d.name)
        for f in files:
            self._lb.insert("end", "  📄  " + f.name)
        self._subs  = subs
        self._files_shown = files
        # colour the file entries dimmer so folders stand out
        base = len(subs)
        for i in range(len(files)):
            self._lb.itemconfig(base + i, fg=FG2)

    def _enter_folder(self, _=None):
        sel = self._lb.curselection()
        if not sel: return
        idx = sel[0]
        if idx < len(self._subs):
            self._history.append(self._current)
            self._current = self._subs[idx]
            self._refresh()
        # clicking a file does nothing (it's display-only)

    def _go_back(self):
        if self._history:
            self._current = self._history.pop()
            self._refresh()

    def _select(self):
        if self.on_select:
            self.on_select(self._current)
        if self.next_cb:
            self.next_cb()

    def _custom_path(self):
        p = filedialog.askdirectory(initialdir=str(self._current))
        if p:
            self._current = Path(p)
            self._refresh()
            if self.on_select:
                self.on_select(self._current)
            if self.next_cb:
                self.next_cb()

    def get(self):
        return self._current

# =============================================================================
#  Preview list widget
# =============================================================================
class PreviewList(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG, **kw)
        top = styled_frame(self)
        top.pack(fill="x")
        label(top, "Preview", font=FONT_H2).pack(side="left")
        self._count_lbl = label(top, "", font=FONT_SM, fg=FG2)
        self._count_lbl.pack(side="right")
        separator(self).pack(fill="x", pady=4)
        frm, self._lb = make_scrolled_list(self, height=14)
        frm.pack(fill="both", expand=True)

    def load(self, rows: list[tuple[str,str,str]]):
        """rows = [(original, arrow, new_name), ...]"""
        self._lb.delete(0, "end")
        for orig, arrow, new in rows:
            self._lb.insert("end", f"  {orig}   {arrow}   {new}")
        self._count_lbl.config(text=f"{len(rows)} file(s)")

    def clear(self):
        self._lb.delete(0, "end")
        self._count_lbl.config(text="")

# =============================================================================
#  Progress / log widget
# =============================================================================
class LogPanel(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG, **kw)
        label(self, "Log", font=FONT_H2).pack(anchor="w")
        separator(self).pack(fill="x", pady=4)
        self._text = tk.Text(self, bg=BG2, fg=FG, font=FONT_MONO,
                             relief="flat", bd=0, state="disabled", height=12)
        sb = tk.Scrollbar(self, command=self._text.yview, bg=BG2)
        self._text.config(yscrollcommand=sb.set)
        self._text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._text.tag_config("ok",  foreground=FG_GREEN)
        self._text.tag_config("err", foreground=FG_RED)
        self._text.tag_config("warn",foreground=FG_YELLOW)
        self._text.tag_config("dim", foreground=FG2)

    def log(self, msg, tag=""):
        self._text.config(state="normal")
        self._text.insert("end", msg + "\n", tag)
        self._text.see("end")
        self._text.config(state="disabled")

    def clear(self):
        self._text.config(state="normal")
        self._text.delete("1.0","end")
        self._text.config(state="disabled")

# =============================================================================
#  Image preview pane (live thumbnail + info)
# =============================================================================
class ImagePreviewPane(tk.Frame):
    """Right-side live preview for Compress/Resize settings pages.
    Call load_image(path) to set source, then update_preview(render_fn)
    where render_fn(img) -> PIL.Image of the result.
    Debounces rapid calls with 250 ms delay."""

    THUMB_W = 340
    THUMB_H = 280

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG2, **kw)
        self._src_path  = None
        self._src_img   = None
        self._tk_img    = None
        self._after_id  = None
        self._build()

    def _build(self):
        label(self, "Image preview", font=FONT_H2, bg=BG2).pack(pady=(10,4))
        separator(self).pack(fill="x", padx=10)

        # File picker row
        pick_row = styled_frame(self, bg=BG2)
        pick_row.pack(fill="x", padx=10, pady=(6,0))
        self._file_lbl = label(pick_row, "No image selected", fg=FG2, font=FONT_SM, bg=BG2)
        self._file_lbl.pack(side="left", fill="x", expand=True)
        btn(pick_row, "Pick image", self._pick, primary=False).pack(side="right")

        # Canvas
        self._canvas = tk.Canvas(self, width=self.THUMB_W, height=self.THUMB_H,
                                  bg=BG3, highlightthickness=0)
        self._canvas.pack(padx=10, pady=8)
        self._canvas.create_text(self.THUMB_W//2, self.THUMB_H//2,
                                  text="Select an image to preview",
                                  fill=FG2, font=FONT_SM, tags="placeholder")

        # Info labels
        self._info_orig = label(self, "", fg=FG2, font=FONT_SM, bg=BG2)
        self._info_orig.pack()
        self._info_new  = label(self, "", fg=FG_GREEN, font=FONT_SM, bg=BG2)
        self._info_new.pack(pady=(0,8))

    def _pick(self):
        p = filedialog.askopenfilename(
            title="Pick a preview image",
            initialdir=str(DESKTOP),
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp"), ("All", "*.*")])
        if p:
            self.load_image(Path(p))

    def load_image(self, path: Path):
        try:
            img = Image.open(path)
            img.load()   # force decode
            self._src_path = path
            self._src_img  = img
            self._file_lbl.config(text=path.name)
            self._info_orig.config(
                text=f"Original: {img.width}×{img.height}  {fmt_size(path.stat().st_size)}")
            self._info_new.config(text="")
            self._show_image(img)
        except Exception:
            self._src_img = None

    def _show_image(self, img: Image.Image):
        """Fit image into canvas and display."""
        self._canvas.delete("placeholder")
        display = img.copy()
        display.thumbnail((self.THUMB_W, self.THUMB_H), Image.LANCZOS)
        try:
            from PIL import ImageTk
            self._tk_img = ImageTk.PhotoImage(display)
            self._canvas.delete("all")
            cw, ch = self.THUMB_W, self.THUMB_H
            self._canvas.create_image(cw//2, ch//2, anchor="center", image=self._tk_img)
        except Exception:
            pass

    def schedule_update(self, render_fn, delay_ms=280):
        """Call render_fn() in a background thread after debounce delay."""
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(delay_ms, lambda: self._do_update(render_fn))

    def _do_update(self, render_fn):
        self._after_id = None
        if self._src_img is None:
            return
        import io, threading
        def worker():
            try:
                result_img, info_str = render_fn(self._src_img.copy(), self._src_path)
                self.after(0, lambda: self._apply(result_img, info_str))
            except Exception as e:
                self.after(0, lambda: self._info_new.config(
                    text=f"Error: {e}", fg=FG_RED))
        threading.Thread(target=worker, daemon=True).start()

    def _apply(self, result_img, info_str):
        self._show_image(result_img)
        self._info_new.config(text=info_str, fg=FG_GREEN)

    def clear(self):
        self._src_img  = None
        self._src_path = None
        self._file_lbl.config(text="No image selected")
        self._info_orig.config(text="")
        self._info_new.config(text="")
        self._canvas.delete("all")
        self._canvas.create_text(self.THUMB_W//2, self.THUMB_H//2,
                                  text="Select an image to preview",
                                  fill=FG2, font=FONT_SM, tags="placeholder")

# =============================================================================
#  Step navigator (breadcrumb)
# =============================================================================
class StepBar(tk.Frame):
    def __init__(self, parent, steps, **kw):
        super().__init__(parent, bg=BG, **kw)
        self._labels = []
        for i, s in enumerate(steps):
            if i > 0:
                tk.Label(self, text="›", bg=BG, fg=FG2,
                         font=("Segoe UI",11)).pack(side="left", padx=2)
            lbl = tk.Label(self, text=s, bg=BG, fg=FG2, font=FONT_SM, padx=6, pady=2)
            lbl.pack(side="left")
            self._labels.append(lbl)

    def set_step(self, idx):
        for i, lbl in enumerate(self._labels):
            if i == idx:
                lbl.config(fg=FG, bg=ACCENT,
                           font=("Segoe UI",9,"bold"))
            elif i < idx:
                lbl.config(fg=FG_GREEN, bg=BG,
                           font=("Segoe UI",9))
            else:
                lbl.config(fg=FG2, bg=BG,
                           font=("Segoe UI",9))

# =============================================================================
#  Base wizard panel
# =============================================================================
class WizardPanel(tk.Frame):
    """One step in a wizard. Call show()/hide()."""
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG, **kw)

    def show(self): self.pack(fill="both", expand=True)
    def hide(self): self.pack_forget()

# =============================================================================
#  TOOL: RENAME
# =============================================================================
class RenameWizard(tk.Toplevel):
    STEPS = ["1 · Folder", "2 · Filter", "3 · Settings", "4 · Preview", "5 · Done"]

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Image Tool  —  Rename")
        self.configure(bg=BG)
        self.geometry("860x680")
        self.resizable(True, True)
        self._step   = 0
        self._folder = None
        self._files  = []
        self._cfg    = {}
        self._build()
        self._goto(0)

    def _build(self):
        # Header
        hdr = styled_frame(self)
        hdr.pack(fill="x", padx=20, pady=(16,0))
        label(hdr, "✏  Rename", font=FONT_H1).pack(side="left")
        self._step_bar = StepBar(hdr, self.STEPS)
        self._step_bar.pack(side="right")

        separator(self).pack(fill="x", padx=20, pady=10)

        # Content area
        self._content = styled_frame(self)
        self._content.pack(fill="both", expand=True, padx=20)

        # Nav bar
        separator(self).pack(fill="x", padx=20, pady=10)
        nav = styled_frame(self)
        nav.pack(fill="x", padx=20, pady=(0,16))
        self._back_btn = btn(nav, "← Back", self._back, primary=False)
        self._back_btn.pack(side="left")
        self._next_btn = btn(nav, "Next →", self._next, primary=True)
        self._next_btn.pack(side="right")

        # Build step frames
        self._s0 = self._build_s0()
        self._s1 = self._build_s1()
        self._s2 = self._build_s2()
        self._s3 = self._build_s3()
        self._s4 = self._build_s4()
        self._panels = [self._s0, self._s1, self._s2, self._s3, self._s4]

    # ── Step 0: folder ────────────────────────────────────────────────────────
    def _build_s0(self):
        p = WizardPanel(self._content)
        label(p, "Select a folder", font=FONT_H2).pack(anchor="w", pady=(0,8))
        self._browser = FolderBrowser(p, next_cb=self._next)
        self._browser.pack(fill="both", expand=True)
        return p

    # ── Step 1: filter ────────────────────────────────────────────────────────
    def _build_s1(self):
        p = WizardPanel(self._content)
        label(p, "File filter", font=FONT_H2).pack(anchor="w", pady=(0,10))

        self._ext_var = tk.StringVar(value="*.jpg")
        exts = ["*.jpg", "*.png", "*.jpg + *.png", "*.*", "custom…"]
        for e in exts:
            tk.Radiobutton(p, text=e, variable=self._ext_var, value=e,
                           bg=BG, fg=FG, selectcolor=ACCENT,
                           activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=1)
        frm = styled_frame(p)
        frm.pack(anchor="w", fill="x", pady=(4,0))
        label(frm, "Custom ext:").pack(side="left")
        self._custom_ext = entry(frm, width=12)
        self._custom_ext.pack(side="left", padx=6)

        separator(p).pack(fill="x", pady=10)
        self._recurse_var = tk.BooleanVar(value=False)
        tk.Checkbutton(p, text="Include subfolders (recursive)",
                       variable=self._recurse_var,
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w")
        self._perfolder_var = tk.BooleanVar(value=False)
        tk.Checkbutton(p, text="Process each subfolder independently",
                       variable=self._perfolder_var,
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=4)
        return p

    # ── Step 2: rename settings ───────────────────────────────────────────────
    def _build_s2(self):
        p = WizardPanel(self._content)
        canvas = tk.Canvas(p, bg=BG, highlightthickness=0)
        sb     = tk.Scrollbar(p, command=canvas.yview, bg=BG2)
        canvas.config(yscrollcommand=sb.set)
        inner  = styled_frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.config(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0), window=inner, anchor="nw")
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Prefix / suffix
        label(inner, "Prefix / Suffix", font=FONT_H2).pack(anchor="w", pady=(0,6))
        r1 = styled_frame(inner); r1.pack(fill="x", pady=2)
        label(r1, "Prefix:").pack(side="left")
        self._prefix = entry(r1, width=20); self._prefix.pack(side="left", padx=6)
        label(r1, "Suffix:").pack(side="left", padx=(12,0))
        self._suffix = entry(r1, width=20); self._suffix.pack(side="left", padx=6)

        separator(inner).pack(fill="x", pady=10)

        # Number mode
        label(inner, "Number handling", font=FONT_H2).pack(anchor="w", pady=(0,6))
        self._num_mode = tk.StringVar(value="1")
        modes = [
            ("1", "Keep original filename as-is"),
            ("2", "Keep only the first digits  (e.g.  03_2nd  →  03)"),
            ("3", "Replace with counter  (001, 002, …)"),
            ("4", "Map file numbers to a custom name list"),
        ]
        for val, txt in modes:
            tk.Radiobutton(inner, text=txt, variable=self._num_mode, value=val,
                           bg=BG, fg=FG, selectcolor=ACCENT,
                           activebackground=BG, font=FONT_BODY,
                           command=self._refresh_num_opts).pack(anchor="w", pady=1)

        self._num_opts = styled_frame(inner)
        self._num_opts.pack(anchor="w", fill="x", pady=(4,0), padx=20)

        # counter opts
        self._ctr_frame = styled_frame(self._num_opts)
        r = styled_frame(self._ctr_frame); r.pack(fill="x")
        label(r, "Start at:").pack(side="left")
        self._ctr_start = entry(r, width=6); self._ctr_start.insert(0,"1")
        self._ctr_start.pack(side="left", padx=4)
        label(r, "Zero-pad width:").pack(side="left", padx=(10,0))
        self._ctr_pad = entry(r, width=4); self._ctr_pad.insert(0,"3")
        self._ctr_pad.pack(side="left", padx=4)

        # map opts
        self._map_frame = styled_frame(self._num_opts)
        label(self._map_frame, "Name list (comma-separated):", fg=FG2, font=FONT_SM).pack(anchor="w")
        self._name_map_txt = tk.Text(self._map_frame, bg=BG3, fg=FG, font=FONT_MONO,
                                     height=3, relief="flat", bd=4,
                                     insertbackground=FG)
        self._name_map_txt.pack(fill="x", pady=2)
        label(self._map_frame, "Example:  E1-1, E1-2, E2-1, E2-2", fg=FG2, font=FONT_SM).pack(anchor="w")
        r2 = styled_frame(self._map_frame); r2.pack(fill="x", pady=4)
        label(r2, "File numbering starts at:").pack(side="left")
        self._map_start = entry(r2, width=6); self._map_start.insert(0,"1")
        self._map_start.pack(side="left", padx=4)

        separator(inner).pack(fill="x", pady=10)

        # Find/replace
        label(inner, "Find & Replace  (optional)", font=FONT_H2).pack(anchor="w", pady=(0,6))
        r3 = styled_frame(inner); r3.pack(fill="x", pady=2)
        label(r3, "Find:").pack(side="left")
        self._find = entry(r3, width=20); self._find.pack(side="left", padx=6)
        label(r3, "Replace with:").pack(side="left", padx=(12,0))
        self._replace = entry(r3, width=20); self._replace.pack(side="left", padx=6)

        separator(inner).pack(fill="x", pady=10)

        # Skip
        label(inner, "Skip files  (optional)", font=FONT_H2).pack(anchor="w", pady=(0,6))
        r4 = styled_frame(inner); r4.pack(fill="x", pady=2)
        label(r4, "Skip filenames containing:").pack(side="left")
        self._skip = entry(r4, width=24); self._skip.pack(side="left", padx=6)

        self._refresh_num_opts()
        return p

    def _refresh_num_opts(self):
        for w in self._num_opts.winfo_children():
            w.pack_forget()
        m = self._num_mode.get()
        if m == "3": self._ctr_frame.pack(fill="x")
        if m == "4": self._map_frame.pack(fill="x")

    # ── Step 3: preview ───────────────────────────────────────────────────────
    def _build_s3(self):
        p = WizardPanel(self._content)

        hdr_row = styled_frame(p)
        hdr_row.pack(fill="x", pady=(0, 8))
        label(hdr_row, "Preview  —  review before renaming", font=FONT_H2).pack(side="left")
        self._preview_count_lbl = label(hdr_row, "", fg=FG2, font=FONT_SM)
        self._preview_count_lbl.pack(side="right")

        self._preview = PreviewList(p)
        self._preview.pack(fill="both", expand=True)

        confirm_row = styled_frame(p)
        confirm_row.pack(fill="x", pady=(10, 0))
        label(confirm_row,
              "⚠  Renaming is permanent. Review the list above, then click  ✔ Execute  to proceed.",
              fg=FG_YELLOW, font=FONT_SM).pack(side="left")
        return p

    # ── Step 4: done ──────────────────────────────────────────────────────────
    def _build_s4(self):
        p = WizardPanel(self._content)
        self._log = LogPanel(p)
        self._log.pack(fill="both", expand=True)
        return p

    # ── Navigation ────────────────────────────────────────────────────────────
    def _goto(self, step):
        for pan in self._panels: pan.hide()
        self._step = step
        self._panels[step].show()
        self._step_bar.set_step(step)
        self._back_btn.config(state="normal" if step > 0 else "disabled")
        last = len(self._panels) - 1
        if step == last:
            self._next_btn.config(text="↩  New rename", command=lambda: self._goto(0))
        elif step == last - 1:
            self._next_btn.config(text="✔  Execute", command=self._execute)
        else:
            self._next_btn.config(text="Next →", command=self._next)

    def _back(self): self._goto(max(0, self._step - 1))

    def _next(self):
        step = self._step
        if step == 0:
            self._folder = self._browser.get()
            self._goto(1)
        elif step == 1:
            if not self._load_files(): return
            self._goto(2)
        elif step == 2:
            self._build_cfg()
            self._build_preview()
            self._goto(3)
        elif step == 3:
            self._goto(4)

    def _load_files(self):
        ext_choice = self._ext_var.get()
        if ext_choice == "*.jpg + *.png":
            exts = ["*.jpg","*.png"]
        elif ext_choice == "custom…":
            c = self._custom_ext.get().strip().lstrip("*.")
            exts = [f"*.{c}"] if c else ["*.*"]
        elif ext_choice == "*.*":
            exts = ["*.*"]
        else:
            exts = [ext_choice]

        recurse = self._recurse_var.get()
        self._files = collect_files(self._folder, exts, recurse)
        if not self._files:
            messagebox.showwarning("No files", f"No matching files found in:\n{self._folder}")
            return False
        return True

    def _build_cfg(self):
        nm = self._num_mode.get()
        name_map = [x.strip() for x in
                    re.split(r'[,、]', self._name_map_txt.get("1.0","end"))
                    if x.strip()]
        try: start = int(self._ctr_start.get())
        except: start = 1
        try: pad = int(self._ctr_pad.get())
        except: pad = 3
        try: map_start = int(self._map_start.get())
        except: map_start = 1
        self._cfg = {
            "prefix":    self._prefix.get(),
            "suffix":    self._suffix.get(),
            "num_mode":  nm,
            "start_num": start,
            "pad_width": pad,
            "name_map":  name_map,
            "map_start": map_start,
            "find_text": self._find.get(),
            "replace_text": self._replace.get(),
            "skip":      self._skip.get().strip(),
        }

    def _build_preview(self):
        skip = self._cfg.get("skip","")
        files = [f for f in self._files if skip not in f.name] if skip else self._files
        c = [self._cfg["start_num"]]
        rows = []
        for f in files:
            new = compute_new_name(f, self._cfg, c)
            rows.append((f.name, "→", new))
        self._preview.load(rows)
        self._filtered_files = files
        n = len(files)
        if hasattr(self, "_preview_count_lbl"):
            self._preview_count_lbl.config(text=f"{n} file(s)")

    def _execute(self):
        self._goto(4)
        self._log.clear()
        skip     = self._cfg.get("skip","")
        files    = [f for f in self._files if skip not in f.name] if skip else self._files
        counter  = [self._cfg["start_num"]]
        success  = 0; failures = 0
        for f in files:
            try:
                new_name = compute_new_name(f, self._cfg, counter)
                f.rename(f.parent / new_name)
                self._log.log(f"  {f.name}  →  {new_name}", "ok")
                success += 1
            except Exception as e:
                self._log.log(f"  FAILED: {f.name} — {e}", "err")
                failures += 1
        self._log.log(f"\n  Done — {success} file(s) renamed.", "ok")
        if failures:
            self._log.log(f"  {failures} file(s) failed.", "err")
        self._next_btn.config(text="↩  New rename", command=lambda: self._goto(0))

# =============================================================================
#  TOOL: COMPRESS
# =============================================================================
class CompressWizard(tk.Toplevel):
    STEPS = ["1 · Folder", "2 · Filter", "3 · Settings", "4 · Output", "5 · Preview", "6 · Done"]

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Image Tool  —  Compress")
        self.configure(bg=BG)
        self.geometry("1100x720")
        self.resizable(True, True)
        self._step   = 0
        self._folder = None
        self._files  = []
        self._build()
        self._goto(0)

    def _build(self):
        hdr = styled_frame(self)
        hdr.pack(fill="x", padx=20, pady=(16,0))
        label(hdr, "🗜  Compress", font=FONT_H1).pack(side="left")
        self._step_bar = StepBar(hdr, self.STEPS)
        self._step_bar.pack(side="right")
        separator(self).pack(fill="x", padx=20, pady=10)
        self._content = styled_frame(self)
        self._content.pack(fill="both", expand=True, padx=20)
        separator(self).pack(fill="x", padx=20, pady=10)
        nav = styled_frame(self)
        nav.pack(fill="x", padx=20, pady=(0,16))
        self._back_btn = btn(nav, "← Back", self._back, primary=False)
        self._back_btn.pack(side="left")
        self._next_btn = btn(nav, "Next →", self._next, primary=True)
        self._next_btn.pack(side="right")

        self._s0 = self._build_s0()
        self._s1 = self._build_s1()
        self._s2 = self._build_s2()
        self._s3 = self._build_s3()
        self._s4 = self._build_s4()
        self._s5 = self._build_s5()
        self._panels = [self._s0,self._s1,self._s2,self._s3,self._s4,self._s5]

    def _build_s0(self):  # Compress wizard
        p = WizardPanel(self._content)
        label(p, "Select a folder", font=FONT_H2).pack(anchor="w", pady=(0,8))
        self._browser = FolderBrowser(p, next_cb=self._next)
        self._browser.pack(fill="both", expand=True)
        return p

    def _build_s1(self):
        p = WizardPanel(self._content)
        label(p, "File filter", font=FONT_H2).pack(anchor="w", pady=(0,10))
        self._ext_var = tk.StringVar(value="*.jpg")
        for e in ["*.jpg","*.png","*.jpg + *.png","custom…"]:
            tk.Radiobutton(p, text=e, variable=self._ext_var, value=e,
                           bg=BG, fg=FG, selectcolor=ACCENT,
                           activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=1)
        frm = styled_frame(p); frm.pack(anchor="w", fill="x", pady=4)
        label(frm, "Custom ext:").pack(side="left")
        self._custom_ext = entry(frm, width=12)
        self._custom_ext.pack(side="left", padx=6)
        separator(p).pack(fill="x", pady=10)
        self._recurse_var = tk.BooleanVar(value=False)
        tk.Checkbutton(p, text="Include subfolders (recursive)",
                       variable=self._recurse_var,
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w")
        return p

    def _build_s2(self):  # Compress: settings + live image preview split
        p = WizardPanel(self._content)
        split = styled_frame(p)
        split.pack(fill="both", expand=True)
        left  = styled_frame(split)
        left.pack(side="left", fill="both", expand=True, padx=(0,8))
        right = ImagePreviewPane(split, width=370)
        right.pack(side="right", fill="y")
        self._comp_preview_pane = right

        label(left, "Compression settings", font=FONT_H2).pack(anchor="w", pady=(0,10))
        self._comp_mode = tk.StringVar(value="1")
        tk.Radiobutton(left, text="Fixed quality  — set quality once for all files",
                       variable=self._comp_mode, value="1",
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY,
                       command=self._refresh_comp).pack(anchor="w")
        tk.Radiobutton(left, text="Target file size  — quality auto-adjusted per file",
                       variable=self._comp_mode, value="2",
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY,
                       command=self._refresh_comp).pack(anchor="w", pady=2)

        self._comp_opts = styled_frame(left)
        self._comp_opts.pack(fill="x", pady=(6,0), padx=20)

        # fixed quality
        self._fq_frame = styled_frame(self._comp_opts)
        r = styled_frame(self._fq_frame); r.pack(fill="x")
        label(r, "JPEG quality  (1–100):").pack(side="left")
        self._quality_var = tk.IntVar(value=85)
        sc = tk.Scale(r, from_=1, to=100, orient="horizontal",
                      variable=self._quality_var,
                      bg=BG, fg=FG, troughcolor=BG3,
                      highlightthickness=0, length=180)
        sc.pack(side="left", padx=8)
        self._q_lbl = label(r, "85", fg=FG_GREEN)
        self._q_lbl.pack(side="left")
        def _on_q(*_):
            self._q_lbl.config(text=str(self._quality_var.get()))
            self._trigger_comp_preview()
        self._quality_var.trace_add("write", _on_q)

        # target size
        self._ts_frame = styled_frame(self._comp_opts)
        r2 = styled_frame(self._ts_frame); r2.pack(fill="x")
        label(r2, "Max file size (KB):").pack(side="left")
        self._target_kb = entry(r2, width=8); self._target_kb.insert(0,"500")
        self._target_kb.pack(side="left", padx=6)
        r3 = styled_frame(self._ts_frame); r3.pack(fill="x", pady=4)
        label(r3, "Min quality floor:").pack(side="left")
        self._min_q_var = tk.IntVar(value=20)
        tk.Scale(r3, from_=1, to=95, orient="horizontal",
                 variable=self._min_q_var,
                 bg=BG, fg=FG, troughcolor=BG3,
                 highlightthickness=0, length=160).pack(side="left", padx=8)

        separator(left).pack(fill="x", pady=10)
        label(left, "PNG handling", font=FONT_H2).pack(anchor="w", pady=(0,4))
        label(left, "PNG is lossless — compression has limited effect.",
              fg=FG2, font=FONT_SM).pack(anchor="w")
        self._png_mode = tk.StringVar(value="skip")
        tk.Radiobutton(left, text="Skip PNG files",
                       variable=self._png_mode, value="skip",
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=1)
        tk.Radiobutton(left, text="Keep as PNG  (lossless optimise)",
                       variable=self._png_mode, value="keepng",
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=1)
        tk.Radiobutton(left, text="Convert PNG → JPEG  (loses transparency)",
                       variable=self._png_mode, value="convert",
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w")

        self._refresh_comp()
        return p

    def _refresh_comp(self):
        for w in self._comp_opts.winfo_children(): w.pack_forget()
        if self._comp_mode.get() == "1": self._fq_frame.pack(fill="x")
        else: self._ts_frame.pack(fill="x")
        self._trigger_comp_preview()

    def _trigger_comp_preview(self):
        import io
        pane = getattr(self, '_comp_preview_pane', None)
        if pane is None or pane._src_img is None: return
        mode = self._comp_mode.get()
        q    = self._quality_var.get()
        try: tkb = int(re.sub(r'[^0-9]','', self._target_kb.get()))
        except: tkb = 500
        minq = self._min_q_var.get()
        def render(img: Image.Image, src_path: Path):
            orig_size = src_path.stat().st_size if src_path and src_path.exists() else 0
            work = img.copy()
            if work.mode not in ("RGB","L"):
                bg2 = Image.new("RGB", work.size, (255,255,255))
                try: bg2.paste(work, mask=work.split()[-1])
                except: bg2.paste(work)
                work = bg2
            elif work.mode != "RGB":
                work = work.convert("RGB")
            if mode == "1":
                use_q = q
            else:
                if orig_size and orig_size <= tkb*1024:
                    use_q = 95
                elif src_path and src_path.exists():
                    use_q, _ = find_quality_for_target(src_path, tkb, minq)
                else:
                    use_q = minq
            buf = io.BytesIO()
            work.save(buf, "JPEG", quality=use_q, optimize=True)
            new_size = buf.tell()
            result = Image.open(io.BytesIO(buf.getvalue()))
            saving = round((1 - new_size/max(orig_size,1))*100) if orig_size else 0
            info = f"Q{use_q}  →  {fmt_size(new_size)}  (-{saving}%)"
            return result, info
        pane.schedule_update(render)

    def _build_s3(self):
        p = WizardPanel(self._content)
        label(p, "Output", font=FONT_H2).pack(anchor="w", pady=(0,10))
        self._out_mode = tk.StringVar(value="subfolder")
        tk.Radiobutton(p, text="Save copies to a subfolder  (safe)",
                       variable=self._out_mode, value="subfolder",
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w")
        tk.Radiobutton(p, text="Overwrite original files",
                       variable=self._out_mode, value="overwrite",
                       bg=BG, fg=FG_YELLOW, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=2)
        frm = styled_frame(p); frm.pack(anchor="w", fill="x", pady=6)
        label(frm, "Subfolder name:").pack(side="left")
        self._subfolder_name = entry(frm, width=20)
        self._subfolder_name.insert(0,"compressed")
        self._subfolder_name.pack(side="left", padx=6)
        return p

    def _build_s4(self):
        p = WizardPanel(self._content)
        self._preview = PreviewList(p)
        self._preview.pack(fill="both", expand=True)
        return p

    def _build_s5(self):
        p = WizardPanel(self._content)
        self._log = LogPanel(p)
        self._log.pack(fill="both", expand=True)
        return p

    def _goto(self, step):
        for pan in self._panels: pan.hide()
        self._step = step
        self._panels[step].show()
        self._step_bar.set_step(step)
        self._back_btn.config(state="normal" if step > 0 else "disabled")
        last = len(self._panels) - 1
        if step == last:
            self._next_btn.config(text="↩  New compression", command=lambda: self._goto(0))
        elif step == last - 1:
            self._next_btn.config(text="✔  Execute", command=self._execute)
        else:
            self._next_btn.config(text="Next →", command=self._next)

    def _back(self): self._goto(max(0, self._step - 1))

    def _next(self):
        s = self._step
        if s == 0:   self._folder = self._browser.get(); self._goto(1)
        elif s == 1:
            if not self._load_files(): return
            self._goto(2)
        elif s == 2: self._goto(3)
        elif s == 3:
            self._build_preview(); self._goto(4)
        elif s == 4: self._goto(5)

    def _load_files(self):
        ext_choice = self._ext_var.get()
        if ext_choice == "*.jpg + *.png": exts = ["*.jpg","*.png"]
        elif ext_choice == "custom…":
            c = self._custom_ext.get().strip().lstrip("*.")
            exts = [f"*.{c}"] if c else ["*.*"]
        else: exts = [ext_choice]
        self._files = collect_files(self._folder, exts, self._recurse_var.get())
        if not self._files:
            messagebox.showwarning("No files", f"No matching files in:\n{self._folder}")
            return False
        return True

    def _build_preview(self):
        import io
        rows = []
        mode     = self._comp_mode.get()
        png_mode = self._png_mode.get()
        for f in self._files[:20]:
            orig = f.stat().st_size
            ext  = f.suffix.lower()
            is_png = (ext == ".png")
            if is_png and png_mode == "skip":
                rows.append((f.name, "—", "SKIPPED (PNG)"))
                continue
            if is_png and png_mode == "keepng":
                rows.append((f.name, "→", f"{fmt_size(orig)}  (PNG kept, lossless optimise)"))
                continue
            try:
                if mode == "1":
                    q = self._quality_var.get()
                    buf = io.BytesIO()
                    img = Image.open(f)
                    if img.mode not in ("RGB","L"):
                        bg = Image.new("RGB", img.size, (255,255,255))
                        try: bg.paste(img, mask=img.split()[-1])
                        except: bg.paste(img)
                        img = bg
                    img.save(buf, "JPEG", quality=q, optimize=True)
                    new_size  = buf.tell()
                    saving    = round((1 - new_size/orig)*100)
                    rows.append((f.name, "→", f"{fmt_size(orig)}  →  {fmt_size(new_size)}  (Q{q}, -{saving}%)"))
                else:
                    try: tkb = int(re.sub(r'[^0-9]','', self._target_kb.get()))
                    except: tkb = 500
                    minq = self._min_q_var.get()
                    if orig <= tkb*1024:
                        rows.append((f.name, "—", f"{fmt_size(orig)}  (already under target)"))
                    else:
                        q, new_size = find_quality_for_target(f, tkb, minq)
                        saving = round((1 - new_size/orig)*100)
                        rows.append((f.name, "→", f"{fmt_size(orig)}  →  {fmt_size(new_size)}  (Q{q}, -{saving}%)"))
            except Exception as e:
                rows.append((f.name, "!", str(e)))
        if len(self._files) > 20:
            rows.append(("…", "", f"and {len(self._files)-20} more file(s)"))
        self._preview.load(rows)

    def _execute(self):
        self._goto(5)
        self._log.clear()
        mode     = self._comp_mode.get()
        overwrite= self._out_mode.get() == "overwrite"
        subname  = self._subfolder_name.get().strip() or "compressed"
        q_fixed  = self._quality_var.get()
        try: tkb = int(re.sub(r'[^0-9]','', self._target_kb.get()))
        except: tkb = 500
        minq      = self._min_q_var.get()
        png_mode  = self._png_mode.get()   # "skip" | "keepng" | "convert"
        success = 0; failures = 0; skipped = 0; saved = 0

        for f in self._files:
            ext    = f.suffix.lower()
            is_png = (ext == ".png")
            if is_png and png_mode == "skip":
                skipped += 1; continue
            orig = f.stat().st_size
            try:
                if is_png and png_mode == "keepng":
                    # Save as optimised PNG — lossless
                    out_name = f.stem + ".png"
                    if overwrite:
                        dst = f
                    else:
                        d = f.parent / subname; d.mkdir(exist_ok=True)
                        dst = d / out_name
                    img = Image.open(f)
                    img.save(dst, "PNG", optimize=True)
                else:
                    out_name = f.stem + ".jpg"
                    if overwrite:
                        dst = f.parent / out_name if is_png else f
                    else:
                        d = f.parent / subname; d.mkdir(exist_ok=True)
                        dst = d / out_name

                    if mode == "1":
                        compress_jpeg_pillow(f, dst, q_fixed)
                    else:
                        if orig <= tkb*1024: skipped += 1; continue
                        q, _ = find_quality_for_target(f, tkb, minq)
                        compress_jpeg_pillow(f, dst, q)

                new_size = dst.stat().st_size
                saved   += max(orig - new_size, 0)
                if overwrite and is_png and png_mode == "convert" and dst.exists() and dst != f:
                    f.unlink()
                self._log.log(f"  {f.name}  →  {fmt_size(orig)} → {fmt_size(new_size)}", "ok")
                success += 1
            except Exception as e:
                self._log.log(f"  FAILED: {f.name} — {e}", "err")
                failures += 1

        total = sum(f.stat().st_size for f in self._files if f.exists())
        pct   = round(saved / max(total,1) * 100)
        self._log.log(f"\n  Done — {success} file(s) compressed.  Saved {fmt_size(max(saved,0))} (-{pct}%)", "ok")
        if skipped:  self._log.log(f"  {skipped} file(s) skipped.", "dim")
        if failures: self._log.log(f"  {failures} file(s) failed.", "err")
        self._next_btn.config(text="↩  New compression", command=lambda: self._goto(0))

# =============================================================================
#  TOOL: RESIZE
# =============================================================================
class ResizeWizard(tk.Toplevel):
    STEPS = ["1 · Folder", "2 · Filter", "3 · Settings", "4 · Output", "5 · Preview", "6 · Done"]

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Image Tool  —  Resize")
        self.configure(bg=BG)
        self.geometry("1100x700")
        self.resizable(True, True)
        self._step   = 0
        self._folder = None
        self._files  = []
        self._build()
        self._goto(0)

    def _build(self):
        hdr = styled_frame(self)
        hdr.pack(fill="x", padx=20, pady=(16,0))
        label(hdr, "⤡  Resize", font=FONT_H1).pack(side="left")
        self._step_bar = StepBar(hdr, self.STEPS)
        self._step_bar.pack(side="right")
        separator(self).pack(fill="x", padx=20, pady=10)
        self._content = styled_frame(self)
        self._content.pack(fill="both", expand=True, padx=20)
        separator(self).pack(fill="x", padx=20, pady=10)
        nav = styled_frame(self)
        nav.pack(fill="x", padx=20, pady=(0,16))
        self._back_btn = btn(nav, "← Back", self._back, primary=False)
        self._back_btn.pack(side="left")
        self._next_btn = btn(nav, "Next →", self._next, primary=True)
        self._next_btn.pack(side="right")

        self._s0 = self._build_s0()
        self._s1 = self._build_s1()
        self._s2 = self._build_s2()
        self._s3 = self._build_s3()
        self._s4 = self._build_s4()
        self._s5 = self._build_s5()
        self._panels = [self._s0,self._s1,self._s2,self._s3,self._s4,self._s5]

    def _build_s0(self):  # Resize wizard
        p = WizardPanel(self._content)
        label(p, "Select a folder", font=FONT_H2).pack(anchor="w", pady=(0,8))
        self._browser = FolderBrowser(p, next_cb=self._next)
        self._browser.pack(fill="both", expand=True)
        return p

    def _build_s1(self):
        p = WizardPanel(self._content)
        label(p, "File filter", font=FONT_H2).pack(anchor="w", pady=(0,10))
        self._ext_var = tk.StringVar(value="*.jpg")
        for e in ["*.jpg","*.png","*.jpg + *.png","custom…"]:
            tk.Radiobutton(p, text=e, variable=self._ext_var, value=e,
                           bg=BG, fg=FG, selectcolor=ACCENT,
                           activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=1)
        frm = styled_frame(p); frm.pack(anchor="w", fill="x", pady=4)
        label(frm, "Custom ext:").pack(side="left")
        self._custom_ext = entry(frm, width=12)
        self._custom_ext.pack(side="left", padx=6)
        separator(p).pack(fill="x", pady=10)
        self._recurse_var = tk.BooleanVar(value=False)
        tk.Checkbutton(p, text="Include subfolders (recursive)",
                       variable=self._recurse_var,
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w")
        return p

    def _build_s2(self):  # Resize: settings + live image preview split
        p = WizardPanel(self._content)
        split = styled_frame(p)
        split.pack(fill="both", expand=True)
        left  = styled_frame(split)
        left.pack(side="left", fill="both", expand=True, padx=(0,8))
        right = ImagePreviewPane(split, width=370)
        right.pack(side="right", fill="y")
        self._resize_preview_pane = right

        label(left, "Resize mode", font=FONT_H2).pack(anchor="w", pady=(0,10))
        self._resize_mode = tk.StringVar(value="1")
        modes = [
            ("1","Fixed width  (keep aspect ratio)"),
            ("2","Fixed height  (keep aspect ratio)"),
            ("3","Fit inside a box  (keep aspect ratio)"),
            ("4","Exact size  (may stretch)"),
            ("5","Scale by percentage"),
        ]
        for val, txt in modes:
            tk.Radiobutton(left, text=txt, variable=self._resize_mode, value=val,
                           bg=BG, fg=FG, selectcolor=ACCENT,
                           activebackground=BG, font=FONT_BODY,
                           command=self._refresh_resize).pack(anchor="w", pady=1)

        self._resize_opts = styled_frame(left)
        self._resize_opts.pack(fill="x", pady=(8,0), padx=20)

        def dim_row(parent, label_txt):
            r = styled_frame(parent); r.pack(fill="x", pady=2)
            label(r, label_txt).pack(side="left")
            e = entry(r, width=8); e.pack(side="left", padx=6)
            label(r, "px", fg=FG2, font=FONT_SM).pack(side="left")
            # trigger preview when user finishes typing
            e.bind("<FocusOut>", lambda _: self._trigger_resize_preview())
            e.bind("<Return>",   lambda _: self._trigger_resize_preview())
            return e

        self._tw = dim_row(self._resize_opts, "Width:")
        self._th = dim_row(self._resize_opts, "Height:")
        self._pct_entry = entry(self._resize_opts, width=6)
        self._pct_entry.bind("<FocusOut>", lambda _: self._trigger_resize_preview())
        self._pct_entry.bind("<Return>",   lambda _: self._trigger_resize_preview())

        separator(left).pack(fill="x", pady=10)
        label(left, "JPEG quality for saved files", font=FONT_H2).pack(anchor="w", pady=(0,6))
        rq = styled_frame(left); rq.pack(fill="x")
        self._jpeg_q = tk.IntVar(value=90)
        sc = tk.Scale(rq, from_=1, to=100, orient="horizontal",
                      variable=self._jpeg_q,
                      bg=BG, fg=FG, troughcolor=BG3,
                      highlightthickness=0, length=180)
        sc.pack(side="left", padx=(0,8))
        ql = label(rq, "90", fg=FG_GREEN); ql.pack(side="left")
        def _on_rq(*_):
            ql.config(text=str(self._jpeg_q.get()))
            self._trigger_resize_preview()
        self._jpeg_q.trace_add("write", _on_rq)

        self._refresh_resize()
        return p

    def _refresh_resize(self):
        m = self._resize_mode.get()
        self._tw.master.pack_forget()
        self._th.master.pack_forget()
        self._pct_entry.pack_forget()
        if m == "1":
            self._tw.master.pack(fill="x", pady=2)
        elif m == "2":
            self._th.master.pack(fill="x", pady=2)
        elif m in ("3","4"):
            self._tw.master.pack(fill="x", pady=2)
            self._th.master.pack(fill="x", pady=2)
        else:
            r = styled_frame(self._resize_opts); r.pack(fill="x", pady=2)
            label(r, "Scale %:").pack(side="left")
            self._pct_entry.pack(side="left", padx=6)
            label(r, "%", fg=FG2, font=FONT_SM).pack(side="left")
        self._trigger_resize_preview()

    def _trigger_resize_preview(self):
        pane = getattr(self, '_resize_preview_pane', None)
        if pane is None or pane._src_img is None: return
        cfg = self._get_resize_cfg()
        def render(img: Image.Image, src_path: Path):
            w, h = img.width, img.height
            try:
                nw, nh = compute_new_size(w, h, cfg)
                if nw <= 0 or nh <= 0:
                    return img, "Invalid dimensions"
            except Exception:
                return img, "Enter valid dimensions"
            result = img.resize((nw, nh), Image.LANCZOS)
            info = f"{w}×{h}  →  {nw}×{nh}"
            return result, info
        pane.schedule_update(render)

    def _build_s3(self):
        p = WizardPanel(self._content)
        label(p, "Output", font=FONT_H2).pack(anchor="w", pady=(0,10))
        self._out_mode = tk.StringVar(value="subfolder")
        tk.Radiobutton(p, text="Save copies to a subfolder  (safe)",
                       variable=self._out_mode, value="subfolder",
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w")
        tk.Radiobutton(p, text="Overwrite original files",
                       variable=self._out_mode, value="overwrite",
                       bg=BG, fg=FG_YELLOW, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=2)
        frm = styled_frame(p); frm.pack(anchor="w", fill="x", pady=6)
        label(frm, "Subfolder name:").pack(side="left")
        self._subfolder_name = entry(frm, width=20)
        self._subfolder_name.insert(0,"resized")
        self._subfolder_name.pack(side="left", padx=6)
        return p

    def _build_s4(self):
        p = WizardPanel(self._content)
        self._preview = PreviewList(p)
        self._preview.pack(fill="both", expand=True)
        return p

    def _build_s5(self):
        p = WizardPanel(self._content)
        self._log = LogPanel(p)
        self._log.pack(fill="both", expand=True)
        return p

    def _goto(self, step):
        for pan in self._panels: pan.hide()
        self._step = step
        self._panels[step].show()
        self._step_bar.set_step(step)
        self._back_btn.config(state="normal" if step > 0 else "disabled")
        last = len(self._panels) - 1
        if step == last:
            self._next_btn.config(text="↩  New resize", command=lambda: self._goto(0))
        elif step == last - 1:
            self._next_btn.config(text="✔  Execute", command=self._execute)
        else:
            self._next_btn.config(text="Next →", command=self._next)

    def _back(self): self._goto(max(0, self._step - 1))

    def _next(self):
        s = self._step
        if s == 0:   self._folder = self._browser.get(); self._goto(1)
        elif s == 1:
            if not self._load_files(): return
            self._goto(2)
        elif s == 2: self._goto(3)
        elif s == 3:
            self._build_preview(); self._goto(4)
        elif s == 4: self._goto(5)

    def _load_files(self):
        ext_choice = self._ext_var.get()
        if ext_choice == "*.jpg + *.png": exts = ["*.jpg","*.png"]
        elif ext_choice == "custom…":
            c = self._custom_ext.get().strip().lstrip("*.")
            exts = [f"*.{c}"] if c else ["*.*"]
        else: exts = [ext_choice]
        self._files = collect_files(self._folder, exts, self._recurse_var.get())
        if not self._files:
            messagebox.showwarning("No files", f"No matching files in:\n{self._folder}")
            return False
        return True

    def _get_resize_cfg(self):
        m = self._resize_mode.get()
        try: tw = int(self._tw.get())
        except: tw = 0
        try: th = int(self._th.get())
        except: th = 0
        try: pct = float(self._pct_entry.get())
        except: pct = 100.0
        return {"resize_mode": m, "target_w": tw, "target_h": th,
                "pct": pct, "jpeg_quality": self._jpeg_q.get()}

    def _build_preview(self):
        cfg  = self._get_resize_cfg()
        rows = []
        for f in self._files[:20]:
            try:
                img = Image.open(f)
                w, h = img.width, img.height
                nw, nh = compute_new_size(w, h, cfg)
                rows.append((f.name, "→", f"{w}×{h}  →  {nw}×{nh}"))
            except Exception as e:
                rows.append((f.name, "!", str(e)))
        if len(self._files) > 20:
            rows.append(("…","",f"and {len(self._files)-20} more"))
        self._preview.load(rows)

    def _execute(self):
        self._goto(5)
        self._log.clear()
        cfg       = self._get_resize_cfg()
        overwrite = self._out_mode.get() == "overwrite"
        subname   = self._subfolder_name.get().strip() or "resized"
        success   = 0; failures = 0

        for f in self._files:
            try:
                if overwrite:
                    dst = f
                else:
                    d = f.parent / subname; d.mkdir(exist_ok=True)
                    dst = d / f.name
                img    = Image.open(f)
                w, h   = img.width, img.height
                nw, nh = compute_new_size(w, h, cfg)
                do_resize(f, dst, cfg)
                self._log.log(f"  {f.name}  →  {nw}×{nh}", "ok")
                success += 1
            except Exception as e:
                self._log.log(f"  FAILED: {f.name} — {e}", "err")
                failures += 1

        self._log.log(f"\n  Done — {success} file(s) resized.", "ok")
        if failures: self._log.log(f"  {failures} file(s) failed.", "err")
        self._next_btn.config(text="↩  New resize", command=lambda: self._goto(0))

# =============================================================================
#  PIPELINE: chain multiple tools
# =============================================================================
class PipelineWizard(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Image Tool  —  Pipeline")
        self.configure(bg=BG)
        self.geometry("500x340")
        self.resizable(False, False)
        self._parent = parent
        self._build()

    def _build(self):
        label(self, "Pipeline  —  chain operations", font=FONT_H1).pack(padx=20, pady=(16,4), anchor="w")
        label(self, "Choose which tools to run in sequence.\nEach tool will open as its own wizard — complete them one by one.",
              fg=FG2, font=FONT_SM).pack(padx=20, anchor="w")
        separator(self).pack(fill="x", padx=20, pady=12)

        self._do_resize    = tk.BooleanVar(value=False)
        self._do_compress  = tk.BooleanVar(value=False)
        self._do_rename    = tk.BooleanVar(value=False)

        for var, text in [
            (self._do_resize,   "⤡  Resize first"),
            (self._do_compress, "🗜  Compress"),
            (self._do_rename,   "✏  Rename"),
        ]:
            tk.Checkbutton(self, text=text, variable=var,
                           bg=BG, fg=FG, selectcolor=ACCENT,
                           activebackground=BG, font=FONT_BODY).pack(padx=40, anchor="w", pady=3)

        separator(self).pack(fill="x", padx=20, pady=12)
        label(self, "Tools will launch in the order shown above.", fg=FG2, font=FONT_SM).pack(padx=20, anchor="w")
        btn(self, "▶  Start pipeline", self._start, primary=True).pack(pady=16)

    def _start(self):
        order = []
        if self._do_resize.get():   order.append(ResizeWizard)
        if self._do_compress.get(): order.append(CompressWizard)
        if self._do_rename.get():   order.append(RenameWizard)
        if not order:
            messagebox.showwarning("Nothing selected", "Select at least one operation.")
            return
        self.destroy()
        self._launch_chain(order, 0)

    def _launch_chain(self, order, idx):
        if idx >= len(order): return
        w = order[idx](self._parent)
        def on_close():
            w.destroy()
            self._launch_chain(order, idx+1)
        w.protocol("WM_DELETE_WINDOW", on_close)

# =============================================================================
#  COLOUR PICKER  —  HSV square + hue bar (Illustrator-style)
# =============================================================================
class ColorPickerDialog(tk.Toplevel):
    """Full colour picker: SV square, hue bar, hex field, RGB sliders.
    Usage:
        dlg = ColorPickerDialog(parent, initial="#ff0000")
        parent.wait_window(dlg)
        result = dlg.result   # "#rrggbb" or None if cancelled
    """
    SQ = 220      # size of the SV square
    HB = 20       # width of the hue bar
    PAD = 8

    def __init__(self, parent, initial="#ffffff", title=None):
        super().__init__(parent)
        self.title(title or _t("cpd.title"))
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        self.result = None

        # Parse initial colour to HSV
        r, g, b = self._hex_to_rgb(initial)
        import colorsys
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        self._h = h   # 0-1
        self._s = s   # 0-1
        self._v = v   # 0-1
        self._dragging_sq  = False
        self._dragging_hue = False

        self._build()
        self._redraw_hue_bar()
        self._redraw_sq()
        self._update_fields_from_hsv()

        self.update_idletasks()
        pw = parent.winfo_rootx()
        py = parent.winfo_rooty()
        w, h_win = self.winfo_reqwidth(), self.winfo_reqheight()
        self.geometry(f"+{pw + 60}+{py + 40}")

    # ── Build ─────────────────────────────────────────────────────────────────
    def _build(self):
        SQ, HB, PAD = self.SQ, self.HB, self.PAD

        main = tk.Frame(self, bg=BG, padx=PAD, pady=PAD)
        main.pack()

        # ── Row 1: SV square + hue bar + current/new swatches ─────────────────
        top = tk.Frame(main, bg=BG)
        top.pack(fill="x")

        self._sq_canvas = tk.Canvas(top, width=SQ, height=SQ,
                                     cursor="crosshair", highlightthickness=1,
                                     highlightbackground=BG3)
        self._sq_canvas.pack(side="left")
        self._sq_canvas.bind("<ButtonPress-1>",   self._sq_press)
        self._sq_canvas.bind("<B1-Motion>",        self._sq_drag)
        self._sq_canvas.bind("<ButtonRelease-1>",  lambda e: setattr(self, "_dragging_sq", False))

        gap = tk.Frame(top, bg=BG, width=PAD)
        gap.pack(side="left")

        self._hue_canvas = tk.Canvas(top, width=HB, height=SQ,
                                      cursor="sb_v_double_arrow", highlightthickness=1,
                                      highlightbackground=BG3)
        self._hue_canvas.pack(side="left")
        self._hue_canvas.bind("<ButtonPress-1>",  self._hue_press)
        self._hue_canvas.bind("<B1-Motion>",       self._hue_drag)
        self._hue_canvas.bind("<ButtonRelease-1>", lambda e: setattr(self, "_dragging_hue", False))

        swatch_col = tk.Frame(top, bg=BG, padx=PAD)
        swatch_col.pack(side="left", fill="y")
        label(swatch_col, "Nouveau", fg=FG2, font=FONT_SM, bg=BG).pack(anchor="w")
        self._new_swatch = tk.Frame(swatch_col, width=52, height=52, bg="#ffffff")
        self._new_swatch.pack(pady=(0, 4))
        label(swatch_col, "Initial", fg=FG2, font=FONT_SM, bg=BG).pack(anchor="w")
        r0, g0, b0 = self._hex_to_rgb("#ffffff")
        self._orig_swatch = tk.Frame(swatch_col, width=52, height=52,
                                     bg=self._rgb_to_hex(r0, g0, b0))
        self._orig_swatch.pack()

        # ── Row 2: fields ─────────────────────────────────────────────────────
        sep = tk.Frame(main, bg=BG3, height=1)
        sep.pack(fill="x", pady=(PAD, 0))

        fields = tk.Frame(main, bg=BG, pady=PAD)
        fields.pack(fill="x")

        # Hex
        hex_row = tk.Frame(fields, bg=BG)
        hex_row.pack(fill="x", pady=2)
        label(hex_row, "Hex :", bg=BG, width=4).pack(side="left")
        self._hex_var = tk.StringVar()
        self._hex_entry = tk.Entry(hex_row, textvariable=self._hex_var,
                                    bg=BG3, fg=FG, insertbackground=FG,
                                    relief="flat", font=FONT_MONO, width=10)
        self._hex_entry.pack(side="left", padx=6)
        self._hex_entry.bind("<Return>",     self._on_hex_enter)
        self._hex_entry.bind("<FocusOut>",   self._on_hex_enter)
        self._live_swatch = tk.Frame(hex_row, width=28, height=22, bg="#ffffff")
        self._live_swatch.pack(side="left", padx=4)

        # RGB sliders
        self._r_var = tk.IntVar(value=255)
        self._g_var = tk.IntVar(value=255)
        self._b_var = tk.IntVar(value=255)
        for lbl_txt, var, col in [("R", self._r_var, "#e05555"),
                                   ("G", self._g_var, "#55c055"),
                                   ("B", self._b_var, "#5588e0")]:
            row = tk.Frame(fields, bg=BG)
            row.pack(fill="x", pady=1)
            label(row, lbl_txt, bg=BG, fg=col, font=("Segoe UI", 9, "bold"),
                  width=2).pack(side="left")
            sl = tk.Scale(row, from_=0, to=255, variable=var, orient="horizontal",
                          bg=BG2, fg=FG, troughcolor=BG3,
                          activebackground=ACCENT, highlightthickness=0, length=180,
                          showvalue=True, resolution=1,
                          command=lambda v, _v=var: self._on_rgb_slide())
            sl.pack(side="left", padx=4)
            tk.Entry(row, textvariable=var, bg=BG3, fg=FG, insertbackground=FG,
                     relief="flat", font=FONT_BODY, width=4).pack(side="left")

        # ── Buttons ───────────────────────────────────────────────────────────
        sep2 = tk.Frame(main, bg=BG3, height=1)
        sep2.pack(fill="x", pady=(PAD, 0))
        btn_row = tk.Frame(main, bg=BG, pady=8)
        btn_row.pack(fill="x")
        btn(btn_row, _t("cpd.cancel"), self._cancel, primary=False).pack(side="right", padx=(6, 0))
        btn(btn_row, "✔  OK", self._ok).pack(side="right")
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())

    # ── Drawing ───────────────────────────────────────────────────────────────
    def _redraw_sq(self):
        import colorsys
        SQ = self.SQ
        # Build the SV gradient image: top-right=pure hue, top-left=white, bottom=black
        from PIL import Image as _Img, ImageTk as _ITk
        img = _Img.new("RGB", (SQ, SQ))
        r_h, g_h, b_h = [int(c * 255) for c in colorsys.hsv_to_rgb(self._h, 1.0, 1.0)]
        px = img.load()
        for x in range(SQ):
            s = x / (SQ - 1)
            for y in range(SQ):
                v = 1.0 - y / (SQ - 1)
                r = int((1 - s) * 255 + s * r_h)
                g = int((1 - s) * 255 + s * g_h)
                b = int((1 - s) * 255 + s * b_h)
                r = int(r * v)
                g = int(g * v)
                b = int(b * v)
                px[x, y] = (r, g, b)
        self._sq_photo = _ITk.PhotoImage(img)
        self._sq_canvas.delete("all")
        self._sq_canvas.create_image(0, 0, anchor="nw", image=self._sq_photo)
        # Draw crosshair
        cx = int(self._s * (SQ - 1))
        cy = int((1 - self._v) * (SQ - 1))
        r_c = 6
        self._sq_canvas.create_oval(cx - r_c, cy - r_c, cx + r_c, cy + r_c,
                                     outline="white", width=2)
        self._sq_canvas.create_oval(cx - r_c - 1, cy - r_c - 1,
                                     cx + r_c + 1, cy + r_c + 1,
                                     outline="black", width=1)

    def _redraw_hue_bar(self):
        import colorsys
        from PIL import Image as _Img, ImageTk as _ITk
        img = _Img.new("RGB", (self.HB, self.SQ))
        px = img.load()
        for y in range(self.SQ):
            h = y / (self.SQ - 1)
            r, g, b = [int(c * 255) for c in colorsys.hsv_to_rgb(h, 1.0, 1.0)]
            for x in range(self.HB):
                px[x, y] = (r, g, b)
        self._hue_photo = _ITk.PhotoImage(img)
        self._hue_canvas.delete("all")
        self._hue_canvas.create_image(0, 0, anchor="nw", image=self._hue_photo)
        self._draw_hue_marker()

    def _draw_hue_marker(self):
        self._hue_canvas.delete("hue_mark")
        y = int(self._h * (self.SQ - 1))
        self._hue_canvas.create_line(0, y, self.HB, y, fill="white",
                                      width=2, tags="hue_mark")
        self._hue_canvas.create_line(0, y + 1, self.HB, y + 1, fill="black",
                                      width=1, tags="hue_mark")

    # ── Interaction ───────────────────────────────────────────────────────────
    def _sq_press(self, event):
        self._dragging_sq = True
        self._sq_pick(event.x, event.y)

    def _sq_drag(self, event):
        if self._dragging_sq:
            self._sq_pick(event.x, event.y)

    def _sq_pick(self, x, y):
        SQ = self.SQ
        self._s = max(0.0, min(1.0, x / (SQ - 1)))
        self._v = max(0.0, min(1.0, 1.0 - y / (SQ - 1)))
        self._redraw_sq()
        self._update_fields_from_hsv()

    def _hue_press(self, event):
        self._dragging_hue = True
        self._hue_pick(event.y)

    def _hue_drag(self, event):
        if self._dragging_hue:
            self._hue_pick(event.y)

    def _hue_pick(self, y):
        self._h = max(0.0, min(1.0, y / (self.SQ - 1)))
        self._redraw_hue_bar()
        self._redraw_sq()
        self._update_fields_from_hsv()

    def _on_rgb_slide(self):
        import colorsys
        r, g, b = self._r_var.get(), self._g_var.get(), self._b_var.get()
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        self._h, self._s, self._v = h, s, v
        self._redraw_hue_bar()
        self._redraw_sq()
        hex_val = self._rgb_to_hex(r, g, b)
        self._hex_var.set(hex_val)
        try:
            self._new_swatch.config(bg=hex_val)
            self._live_swatch.config(bg=hex_val)
        except Exception:
            pass

    def _on_hex_enter(self, _event=None):
        raw = self._hex_var.get().strip()
        if not raw.startswith("#"):
            raw = "#" + raw
        try:
            r, g, b = self._hex_to_rgb(raw)
        except Exception:
            return
        import colorsys
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        self._h, self._s, self._v = h, s, v
        self._r_var.set(r)
        self._g_var.set(g)
        self._b_var.set(b)
        self._redraw_hue_bar()
        self._redraw_sq()
        hex_val = self._rgb_to_hex(r, g, b)
        self._hex_var.set(hex_val)
        try:
            self._new_swatch.config(bg=hex_val)
            self._live_swatch.config(bg=hex_val)
        except Exception:
            pass

    def _update_fields_from_hsv(self):
        import colorsys
        r, g, b = [int(c * 255) for c in colorsys.hsv_to_rgb(self._h, self._s, self._v)]
        hex_val = self._rgb_to_hex(r, g, b)
        self._hex_var.set(hex_val)
        self._r_var.set(r)
        self._g_var.set(g)
        self._b_var.set(b)
        try:
            self._new_swatch.config(bg=hex_val)
            self._live_swatch.config(bg=hex_val)
        except Exception:
            pass

    def _ok(self):
        import colorsys
        r, g, b = [int(c * 255) for c in colorsys.hsv_to_rgb(self._h, self._s, self._v)]
        self.result = self._rgb_to_hex(r, g, b)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

    # ── Colour utils ──────────────────────────────────────────────────────────
    @staticmethod
    def _hex_to_rgb(h: str):
        h = h.lstrip("#")
        if len(h) == 3:
            h = h[0]*2 + h[1]*2 + h[2]*2
        if len(h) != 6:
            return (255, 255, 255)
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    @staticmethod
    def _rgb_to_hex(r, g, b):
        return f"#{r:02x}{g:02x}{b:02x}"


# =============================================================================
#  IMAGE GENERATOR  —  numbered image batch creator
# =============================================================================
_FONTS_DIR      = Path("C:/Windows/Fonts")
_FONTS_DIR_USER = Path(os.path.expanduser("~")) / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"
_FONT_CACHE_FILE = Path(os.path.abspath(__file__)).parent / "_font_cache.json"


def _read_font_name(path: str):
    """Parse the 'name' table of a TTF/OTF file and return (family, style).
    Falls back to ('', 'Regular') on any error."""
    import struct as _st
    try:
        with open(path, 'rb') as fh:
            sig = fh.read(4)
            font_start = 0
            if sig == b'ttcf':        # TrueType Collection
                fh.seek(8)
                n = _st.unpack('>I', fh.read(4))[0]
                if n < 1:
                    return '', 'Regular'
                font_start = _st.unpack('>I', fh.read(4))[0]
            fh.seek(font_start + 4)   # skip sfVersion
            num_tables = _st.unpack('>H', fh.read(2))[0]
            fh.seek(font_start + 12)  # jump to table records
            name_off = None
            for _ in range(num_tables):
                rec = fh.read(16)
                if len(rec) < 16:
                    break
                if rec[:4] == b'name':
                    name_off = _st.unpack('>I', rec[8:12])[0]
                    break
            if name_off is None:
                return '', 'Regular'
            fh.seek(name_off)
            _, count, str_off = _st.unpack('>HHH', fh.read(6))
            recs  = fh.read(count * 12)
            fh.seek(name_off + str_off)
            store = fh.read(32768)   # name strings (max 32 KB)
            best: dict = {}
            for i in range(count):
                b    = i * 12
                plat = _st.unpack('>H', recs[b:b+2])[0]
                nid  = _st.unpack('>H', recs[b+6:b+8])[0]
                nlen = _st.unpack('>H', recs[b+8:b+10])[0]
                noff = _st.unpack('>H', recs[b+10:b+12])[0]
                if nid not in (1, 2):
                    continue
                pri = 2 if plat == 3 else 1   # prefer Windows Unicode platform
                if nid in best and best[nid][0] >= pri:
                    continue
                raw = store[noff:noff + nlen]
                try:
                    s = raw.decode('utf-16-be' if plat == 3 else 'latin-1').strip()
                    if s:
                        best[nid] = (pri, s)
                except Exception:
                    pass
            family = best.get(1, (0, ''))[1]
            style  = best.get(2, (0, 'Regular'))[1] or 'Regular'
            return family.strip(), style.strip()
    except Exception:
        return '', 'Regular'


_FONT_FAMILIES:  dict = {}   # { family: { style: path } }
_INSTALLED_FONTS: dict = {}  # { "Family Style": path }  (flat, for compat)


def _discover_font_families() -> dict:
    """Read every installed TTF/OTF and group by (family, style)."""
    _style_kws = ('Bold Italic', 'Bold', 'Italic', 'Light Italic', 'Light',
                  'Medium Italic', 'Medium', 'Black Italic', 'Black', 'Thin',
                  'Heavy', 'Condensed', 'Expanded', 'Oblique', 'Regular',
                  'Semibold', 'SemiBold', 'ExtraBold', 'ExtraLight', 'Narrow')
    families: dict = {}
    font_dirs = [d for d in [_FONTS_DIR, _FONTS_DIR_USER] if d.exists()]
    for font_dir in font_dirs:
        for ext in ("*.ttf", "*.otf", "*.TTF", "*.OTF"):
            for p in sorted(font_dir.glob(ext)):
                fam, sty = _read_font_name(str(p))
                if not fam:                   # fallback: parse filename
                    raw = re.sub(r'([a-z])([A-Z])', r'\1 \2',
                                 re.sub(r'[-_]', ' ', p.stem)).strip()
                    sty, fam = 'Regular', raw
                    for kw in _style_kws:
                        if raw.lower().endswith(' ' + kw.lower()):
                            sty = kw
                            fam = raw[:-(len(kw) + 1)].strip()
                            break
                fam = (fam or p.stem).strip()
                sty = (sty or 'Regular').strip()
                families.setdefault(fam, {})
                if sty not in families[fam]:   # first file wins
                    families[fam][sty] = str(p)
    return dict(sorted(families.items()))


def _get_font_families() -> dict:
    global _FONT_FAMILIES, _INSTALLED_FONTS
    if _FONT_FAMILIES:
        return _FONT_FAMILIES
    # ── Try cache first ───────────────────────────────────────────────────────
    if _FONT_CACHE_FILE.exists():
        try:
            cache_mtime = _FONT_CACHE_FILE.stat().st_mtime
            dirs_ok = all(
                not d.exists() or d.stat().st_mtime <= cache_mtime
                for d in [_FONTS_DIR, _FONTS_DIR_USER]
            )
            if dirs_ok:
                with open(_FONT_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                _FONT_FAMILIES  = data["families"]
                _INSTALLED_FONTS = data["flat"]
                return _FONT_FAMILIES
        except Exception:
            pass
    # ── Rebuild from disk ─────────────────────────────────────────────────────
    _FONT_FAMILIES = _discover_font_families() or {"Default": {"Regular": ""}}
    flat = {}
    for fam, styles in _FONT_FAMILIES.items():
        for sty, path in sorted(styles.items()):
            key = fam if sty == 'Regular' else f"{fam} {sty}"
            if key not in flat:
                flat[key] = path
    _INSTALLED_FONTS = dict(sorted(flat.items()))
    # ── Save cache ────────────────────────────────────────────────────────────
    try:
        with open(_FONT_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"families": _FONT_FAMILIES, "flat": _INSTALLED_FONTS},
                      f, ensure_ascii=False)
    except Exception:
        pass
    return _FONT_FAMILIES


def _get_installed_fonts() -> dict:
    _get_font_families()          # ensure both caches populated
    return _INSTALLED_FONTS


def _load_pil_font(font_name: str, size: int):
    from PIL import ImageFont
    fonts = _get_installed_fonts()
    path = fonts.get(font_name, "")
    if path and os.path.isfile(path):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    # fall-back: treat font_name itself as a path or filename
    if os.path.isfile(font_name):
        try:
            return ImageFont.truetype(font_name, size)
        except Exception:
            pass
    candidate = _FONTS_DIR / font_name
    if candidate.exists():
        try:
            return ImageFont.truetype(str(candidate), size)
        except Exception:
            pass
    candidate_user = _FONTS_DIR_USER / font_name
    if candidate_user.exists():
        try:
            return ImageFont.truetype(str(candidate_user), size)
        except Exception:
            pass
    return ImageFont.load_default()


def _hex_to_rgb(hex_color: str):
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = h[0]*2 + h[1]*2 + h[2]*2
    if len(h) != 6:
        return (255, 255, 255)
    try:
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except ValueError:
        return (255, 255, 255)


def _apply_rounded_mask(img, radius: int):
    from PIL import Image as _Img, ImageDraw as _IDrw
    img = img.convert("RGBA")
    mask = _Img.new("L", img.size, 0)
    draw = _IDrw.Draw(mask)
    try:
        draw.rounded_rectangle([0, 0, img.width, img.height], radius=radius, fill=255)
    except TypeError:
        r, w, h = radius, img.width, img.height
        draw.rectangle([r, 0, w - r, h], fill=255)
        draw.rectangle([0, r, w, h - r], fill=255)
        draw.ellipse([0, 0, 2*r, 2*r], fill=255)
        draw.ellipse([w - 2*r, 0, w, 2*r], fill=255)
        draw.ellipse([0, h - 2*r, 2*r, h], fill=255)
        draw.ellipse([w - 2*r, h - 2*r, w, h], fill=255)
    img.putalpha(mask)
    return img


def _parse_num_list(s: str) -> list:
    """Parse '1-30, 55, 50, 60, 100, 200' → ordered list of unique ints."""
    result = []
    seen: set = set()
    for part in s.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part and not part.lstrip().startswith("-"):
            halves = part.split("-", 1)
            try:
                a, b = int(halves[0].strip()), int(halves[1].strip())
                for n in (range(a, b + 1) if a <= b else range(a, b - 1, -1)):
                    if n not in seen:
                        seen.add(n)
                        result.append(n)
            except ValueError:
                pass
        else:
            try:
                n = int(part)
                if n not in seen:
                    seen.add(n)
                    result.append(n)
            except ValueError:
                pass
    return result


def _draw_tracked(drw, text, fnt, cx, cy, fill_rgba, tracking):
    """Draw text centered at (cx,cy) with per-character letter spacing."""
    if not text:
        return
    advances = []
    for ch in text:
        try:
            adv = fnt.getlength(ch)
        except AttributeError:
            bb = drw.textbbox((0, 0), ch, font=fnt)
            adv = bb[2] - bb[0]
        advances.append(int(adv))
    total_w = sum(advances) + tracking * (len(text) - 1)
    x = cx - total_w // 2
    for ch, adv in zip(text, advances):
        drw.text((x, cy), ch, fill=fill_rgba, font=fnt, anchor="lm")
        x += adv + tracking


def _gen_image(number, width, height, bg_mode, bg_color, bg_image_path,
               text_color, font_name, font_size, corner_mode, corner_radius,
               shadow_on=False, shadow_color="#000000", shadow_offset_x=4, shadow_offset_y=4,
               shadow_blur=3,
               pos_manual=False, pos_x=None, pos_y=None,
               auto_fit=False, letter_spacing=0):
    from PIL import Image as _Img, ImageDraw as _IDrw, ImageFilter as _IFilt
    if bg_mode == "color":
        rgb = _hex_to_rgb(bg_color)
        img = _Img.new("RGBA", (width, height), rgb + (255,))
    else:
        bg = _Img.open(bg_image_path).convert("RGBA")
        img = bg.resize((width, height), _Img.LANCZOS)
    draw = _IDrw.Draw(img)
    text = str(number)
    # Auto-fit: shrink font_size so text stays within 88% of canvas
    if auto_fit:
        max_tw, max_th = int(width * 0.88), int(height * 0.88)
        while font_size > 8:
            _f = _load_pil_font(font_name, font_size)
            try:
                _bb = draw.textbbox((0, 0), text, font=_f)
                _tw, _th = _bb[2] - _bb[0], _bb[3] - _bb[1]
            except AttributeError:
                _tw, _th = draw.textsize(text, font=_f)
            # Account for tracking in width check
            if letter_spacing != 0:
                _tw += letter_spacing * max(0, len(text) - 1)
            if _tw <= max_tw and _th <= max_th:
                break
            font_size -= 2
    font = _load_pil_font(font_name, font_size)
    # Centre point
    if pos_manual and pos_x is not None and pos_y is not None:
        cx, cy = int(pos_x), int(pos_y)
    else:
        cx, cy = width // 2, height // 2
    # Drop-shadow
    if shadow_on:
        sh_rgba  = _hex_to_rgb(shadow_color) + (200,)
        sh_layer = _Img.new("RGBA", img.size, (0, 0, 0, 0))
        sh_draw  = _IDrw.Draw(sh_layer)
        if letter_spacing != 0:
            _draw_tracked(sh_draw, text, font,
                          cx + int(shadow_offset_x), cy + int(shadow_offset_y),
                          sh_rgba, letter_spacing)
        else:
            sh_draw.text((cx + int(shadow_offset_x), cy + int(shadow_offset_y)),
                         text, fill=sh_rgba, font=font, anchor="mm")
        if shadow_blur > 0:
            sh_layer = sh_layer.filter(_IFilt.GaussianBlur(radius=max(1, int(shadow_blur))))
        img = _Img.alpha_composite(img, sh_layer)
        draw = _IDrw.Draw(img)
    text_rgba = _hex_to_rgb(text_color) + (255,)
    if letter_spacing != 0:
        _draw_tracked(draw, text, font, cx, cy, text_rgba, letter_spacing)
    else:
        draw.text((cx, cy), text, fill=text_rgba, font=font, anchor="mm")
    if corner_mode == "rounded" and corner_radius > 0:
        img = _apply_rounded_mask(img, corner_radius)
    return img  # always RGBA


# =============================================================================
#  FONT PREVIEW DIALOG
# =============================================================================
class FontPreviewDialog(tk.Toplevel):
    """Scrollable font picker that renders digit samples for every installed font."""
    SAMPLE = "0123456789"

    def __init__(self, parent, current_font="Impact"):
        super().__init__(parent)
        self.title(_t("fpd.title"))
        self.configure(bg=BG)
        self.resizable(True, True)
        self.grab_set()
        self.result = None
        self._all_fonts   = list(_get_installed_fonts().keys())
        self._filtered    = list(self._all_fonts)
        self._photos: dict = {}
        self._build()
        self.update_idletasks()
        pw, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw2, ph2 = parent.winfo_width(), parent.winfo_height()
        w, h = 560, 640
        self.geometry(f"{w}x{h}+{pw+(pw2-w)//2}+{py+(ph2-h)//2}")
        self._rebuild_rows()

    def _build(self):
        top = styled_frame(self)
        top.pack(fill="x", padx=12, pady=(10, 6))
        label(top, _t("fpd.search")).pack(side="left")
        self._search_var = tk.StringVar()
        e = tk.Entry(top, textvariable=self._search_var, bg=BG3, fg=FG,
                     insertbackground=FG, relief="flat", font=FONT_BODY, width=26)
        e.pack(side="left", padx=6)
        self._count_lbl = label(top, "", fg=FG2, font=FONT_SM)
        self._count_lbl.pack(side="left", padx=8)
        self._search_var.trace_add("write", lambda *_: self._filter())
        e.focus_set()

        container = tk.Frame(self, bg=BG)
        container.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self._cv = tk.Canvas(container, bg=BG2, highlightthickness=0)
        sb = tk.Scrollbar(container, orient="vertical", command=self._cv.yview, bg=BG2)
        self._cv.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._cv.pack(side="left", fill="both", expand=True)
        self._cv.bind("<MouseWheel>",
                      lambda e: self._cv.yview_scroll(-1 * (e.delta // 120), "units"))
        self._inner = tk.Frame(self._cv, bg=BG2)
        self._wid = self._cv.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>",
                         lambda e: self._cv.config(scrollregion=self._cv.bbox("all")))
        self._cv.bind("<Configure>",
                      lambda e: self._cv.itemconfig(self._wid, width=e.width))
        _enable_mousewheel_scroll(self._cv)

        bot = styled_frame(self)
        bot.pack(fill="x", padx=12, pady=(0, 10))
        btn(bot, _t("fpd.cancel"), self.destroy, primary=False).pack(side="right", padx=4)

    def _filter(self):
        q = self._search_var.get().lower()
        self._filtered = ([f for f in self._all_fonts if q in f.lower()]
                          if q else list(self._all_fonts))
        self._rebuild_rows()

    def _rebuild_rows(self):
        for w in self._inner.winfo_children():
            w.destroy()
        self._count_lbl.config(text=_t("fpd.count", n=len(self._filtered)))
        for fname in self._filtered:
            row = tk.Frame(self._inner, bg=BG2, cursor="hand2")
            row.pack(fill="x", pady=1)
            name_lbl = tk.Label(row, text=fname, bg=BG2, fg=FG, font=FONT_SM,
                                anchor="w", width=28)
            name_lbl.pack(side="left", padx=(8, 4), pady=3)
            img_lbl = tk.Label(row, bg=BG2, bd=0)
            img_lbl.pack(side="left", padx=4)
            for widget in (row, name_lbl, img_lbl):
                widget.bind("<Button-1>", lambda e, f=fname: self._select(f))
                widget.bind("<Enter>", lambda e, r=row: r.config(bg=BG3))
                widget.bind("<Leave>", lambda e, r=row: r.config(bg=BG2))
            if fname in self._photos:
                img_lbl.config(image=self._photos[fname])
                img_lbl._photo_ref = self._photos[fname]
            else:
                self._render_async(fname, img_lbl)
        self._cv.yview_moveto(0)
        # Re-bind wheel to newly added rows
        if hasattr(self._cv, '_mw_bind_tree'):
            self._cv._mw_bind_tree(self._inner)

    def _render_async(self, fname, lbl):
        def _worker():
            try:
                from PIL import Image as _Img, ImageDraw as _IDrw, ImageTk as _ITk
                font = _load_pil_font(fname, 20)
                dummy = _Img.new("RGBA", (1, 1))
                dd    = _IDrw.Draw(dummy)
                try:
                    bb = dd.textbbox((0, 0), self.SAMPLE, font=font)
                    tw, th = bb[2] - bb[0], bb[3] - bb[1]
                    ox, oy = -bb[0], -bb[1]
                except AttributeError:
                    tw, th = dd.textsize(self.SAMPLE, font=font)
                    ox, oy = 0, 0
                img = _Img.new("RGBA", (max(tw + 16, 1), max(th + 8, 1)), (49, 49, 69, 255))
                _IDrw.Draw(img).text((8 + ox, 4 + oy), self.SAMPLE,
                                     fill=(220, 220, 220, 255), font=font)
                photo = _ITk.PhotoImage(img.convert("RGB"))
                self.after(0, lambda p=photo, f=fname, l=lbl: self._set(f, p, l))
            except Exception:
                pass
        threading.Thread(target=_worker, daemon=True).start()

    def _set(self, fname, photo, lbl):
        self._photos[fname] = photo
        try:
            lbl.config(image=photo)
            lbl._photo_ref = photo
        except Exception:
            pass

    def _select(self, fname):
        self.result = fname
        self.destroy()


# =============================================================================
#  IMAGE GENERATOR WIZARD
# =============================================================================
class ImageGenWizard(tk.Toplevel):
    WIN_W, WIN_H = 1260, 700
    PREV_W = 300   # width of the persistent live-preview panel

    def __init__(self, parent):
        super().__init__(parent)
        self.title(_t("igw.title"))
        self.STEPS = _t("igw.steps")
        self.configure(bg=BG)
        self.resizable(True, True)
        self._preview_after_id = None   # debounce timer for live preview

        # ── State vars ────────────────────────────────────────────────────────
        self._syncing  = False
        self._w_var    = tk.StringVar(value="500")
        self._h_var    = tk.StringVar(value="500")
        self._link_wh  = tk.BooleanVar(value=True)

        self._bg_mode  = tk.StringVar(value="color")
        self._bg_color = tk.StringVar(value="#3a86ff")
        self._bg_img   = tk.StringVar(value="")
        self._bg_pattern: list = []   # list of paths for cycling pattern mode

        self._num_list   = tk.StringVar(value="1-10")
        self._auto_fsize = tk.BooleanVar(value=True)
        self._font_family = tk.StringVar(value="Impact")
        self._font_style  = tk.StringVar(value="Regular")
        self._font_path   = tk.StringVar(value="")  # resolved TTF/OTF path
        self._font_var    = tk.StringVar(value="Impact")  # display name (compat)
        self._fsize       = tk.StringVar(value="120")
        self._tcol        = tk.StringVar(value="#ffffff")
        self._tracking    = tk.IntVar(value=0)   # letter spacing in pixels

        # Drop-shadow
        self._shadow_on      = tk.BooleanVar(value=False)
        self._shadow_col     = tk.StringVar(value="#000000")
        self._shadow_ox      = tk.StringVar(value="4")
        self._shadow_oy      = tk.StringVar(value="4")
        self._shadow_blur    = tk.StringVar(value="3")

        # Manual position
        self._pos_manual     = tk.BooleanVar(value=False)
        self._pos_x          = tk.StringVar(value="0")
        self._pos_y          = tk.StringVar(value="0")

        self._corner   = tk.StringVar(value="sharp")
        self._radius   = tk.StringVar(value="40")

        self._fmt      = tk.StringVar(value="PNG")
        self._quality  = tk.IntVar(value=92)
        self._prefix   = tk.StringVar(value="image_")
        self._suffix   = tk.StringVar(value="")
        self._out_folder = tk.StringVar(value=str(DESKTOP))

        # Guide mark
        self._guide_on     = tk.BooleanVar(value=False)
        self._guide_margin = tk.IntVar(value=40)

        # Thumb grid photos (kept alive after generation)
        self._thumb_photos: list = []
        # Undo — list of Path objects from the last generation
        self._last_gen_files: list = []

        self._step = 0
        self._panels: list = []
        self._build()
        self._wire_preview_vars()
        self._goto(0)

        self.update_idletasks()
        pw, py = parent.winfo_rootx(), parent.winfo_rooty()
        self.geometry(f"{self.WIN_W}x{self.WIN_H}+{pw+30}+{py+20}")

    # ── Scaffold ──────────────────────────────────────────────────────────────
    def _build(self):
        # Header (full width)
        hdr = styled_frame(self)
        hdr.pack(fill="x", padx=20, pady=(16, 0))
        label(hdr, _t("igw.hdr"), font=FONT_H1).pack(side="left")
        self._step_bar = StepBar(hdr, self.STEPS)
        self._step_bar.pack(side="right")
        separator(self).pack(fill="x", padx=20, pady=10)

        # Body: left = wizard steps, right = persistent live preview
        body = styled_frame(self)
        body.pack(fill="both", expand=True, padx=20)

        # Right panel — live preview (always visible)
        right_panel = tk.Frame(body, bg=BG2, width=self.PREV_W)
        right_panel.pack(side="right", fill="y", padx=(12, 0))
        right_panel.pack_propagate(False)
        self._build_side_preview(right_panel)

        # Left panel — step content
        self._content = styled_frame(body)
        self._content.pack(side="left", fill="both", expand=True)

        # Nav bar (full width)
        separator(self).pack(fill="x", padx=20, pady=10)
        nav = styled_frame(self)
        nav.pack(fill="x", padx=20, pady=(0, 16))
        self._back_btn = btn(nav, _t("igw.back"), self._back, primary=False)
        self._back_btn.pack(side="left")
        self._next_btn = btn(nav, _t("igw.next"), self._next, primary=True)
        self._next_btn.pack(side="right")

        self._panels = [
            self._build_s0(),
            self._build_s1(),
            self._build_s2(),
            self._build_s3(),
            self._build_s4(),
            self._build_s5(),
        ]

    def _build_side_preview(self, parent):
        """Persistent live-preview panel visible on every step."""
        label(parent, _t("igw.prev_title"), font=FONT_H2, bg=BG2).pack(
            anchor="w", padx=10, pady=(10, 4))
        sep = tk.Frame(parent, bg=BG3, height=1)
        sep.pack(fill="x", padx=10)
        CW = self.PREV_W - 20
        CH = CW   # square canvas
        self._side_canvas = tk.Canvas(parent, width=CW, height=CH,
                                       bg=BG3, highlightthickness=0)
        self._side_canvas.pack(padx=10, pady=8)
        self._side_canvas.create_text(CW // 2, CH // 2,
                                       text=_t("igw.prev_ph"), fill=FG2,
                                       font=FONT_SM, tags="ph")
        self._side_info = label(parent, "", fg=FG2, font=FONT_SM, bg=BG2,
                                 wraplength=self.PREV_W - 20, justify="left")
        self._side_info.pack(padx=10, fill="x")
        btn(parent, _t("igw.refresh"), self._schedule_preview,
            primary=False).pack(padx=10, pady=(6, 0), fill="x")

    def _wire_preview_vars(self):
        """Attach traces to all relevant vars so any change triggers a debounced preview."""
        watch = [
            self._w_var, self._h_var,
            self._bg_color, self._bg_img, self._bg_mode,
            self._num_list, self._font_path, self._fsize, self._tcol,
            self._shadow_col, self._shadow_ox, self._shadow_oy, self._shadow_blur,
            self._pos_x, self._pos_y,
            self._corner, self._radius, self._guide_margin, self._tracking,
        ]
        for var in watch:
            var.trace_add("write", lambda *_: self._schedule_preview())
        for bvar in [self._shadow_on, self._pos_manual, self._link_wh,
                     self._auto_fsize, self._guide_on]:
            bvar.trace_add("write", lambda *_: self._schedule_preview())

    def _schedule_preview(self, delay_ms=350):
        """Debounced: cancel any pending refresh then start a new one."""
        if self._preview_after_id:
            self.after_cancel(self._preview_after_id)
        self._preview_after_id = self.after(delay_ms, self._run_side_preview)

    def _run_side_preview(self):
        self._preview_after_id = None
        try:
            nums = _parse_num_list(self._num_list.get())
            nf  = nums[0] if nums else 1
            w   = int(self._w_var.get())
            h   = int(self._h_var.get())
            fsz = int(self._fsize.get())
            rad = int(self._radius.get())
        except Exception:
            return
        CW = self.PREV_W - 20
        self._side_canvas.delete("all")
        self._side_canvas.create_text(CW // 2, CW // 2,
                                       text="…", fill=FG2, font=FONT_SM)

        kw = dict(
            number=nf, width=w, height=h,
            bg_mode="image" if self._bg_mode.get() == "pattern" and self._bg_pattern
                    else self._bg_mode.get(),
            bg_color=self._bg_color.get(),
            bg_image_path=(self._bg_pattern[0]
                           if self._bg_mode.get() == "pattern" and self._bg_pattern
                           else self._bg_img.get()),
            text_color=self._tcol.get(),
            font_name=self._font_path.get() or self._font_var.get(),
            font_size=fsz,
            corner_mode=self._corner.get(),
            corner_radius=rad,
            shadow_on=self._shadow_on.get(),
            shadow_color=self._shadow_col.get(),
            shadow_offset_x=int(self._shadow_ox.get() or 0),
            shadow_offset_y=int(self._shadow_oy.get() or 0),
            shadow_blur=int(self._shadow_blur.get() or 0),
            pos_manual=self._pos_manual.get(),
            pos_x=int(self._pos_x.get() or 0),
            pos_y=int(self._pos_y.get() or 0),
            auto_fit=self._auto_fsize.get(),
            letter_spacing=self._tracking.get(),
        )
        guide_on     = self._guide_on.get()
        guide_margin = self._guide_margin.get()

        def _worker():
            try:
                from PIL import Image as _Img, ImageTk as _ITk
                img = _gen_image(**kw)
                thumb = img.copy()
                thumb.thumbnail((CW, CW), _Img.LANCZOS)
                tw, th = thumb.size
                bg_disp = _Img.new("RGBA", thumb.size, (49, 49, 69, 255))
                if thumb.mode == "RGBA":
                    bg_disp.paste(thumb, mask=thumb.split()[3])
                else:
                    bg_disp.paste(thumb)
                photo = _ITk.PhotoImage(bg_disp.convert("RGB"))
                info = f"{img.width}×{img.height}  #{nf}"
                self.after(0, lambda p=photo, i=info, rw=img.width, rh=img.height,
                           tw_=tw, th_=th: self._apply_side_preview(
                               p, i, rw, rh, tw_, th_, guide_on, guide_margin))
            except Exception as exc:
                self.after(0, lambda e=str(exc):
                           self._side_info.config(text=f"⚠ {e}", fg=FG_RED))
        threading.Thread(target=_worker, daemon=True).start()

    def _apply_side_preview(self, photo, info,
                             real_w=0, real_h=0, thumb_w=0, thumb_h=0,
                             guide_on=False, guide_margin=40):
        CW = self.PREV_W - 20
        self._side_canvas.delete("all")
        # Centre offset of the thumbnail on the square canvas
        ox = (CW - thumb_w) // 2
        oy = (CW - thumb_h) // 2
        self._side_canvas.create_image(CW // 2, CW // 2, anchor="center", image=photo)
        self._side_canvas._photo_ref = photo
        # Guide mark overlay
        if guide_on and real_w > 0 and real_h > 0:
            sx = thumb_w / real_w
            sy = thumb_h / real_h
            gx1 = ox + int(guide_margin * sx)
            gy1 = oy + int(guide_margin * sy)
            gx2 = ox + thumb_w - int(guide_margin * sx)
            gy2 = oy + thumb_h - int(guide_margin * sy)
            # Dashed yellow guide rectangle
            self._side_canvas.create_rectangle(
                gx1, gy1, gx2, gy2,
                outline="#f2c94c", width=1, dash=(4, 3), tags="guide")
            # Corner ticks
            tk_len = 6
            for cx_, cy_ in [(gx1, gy1), (gx2, gy1), (gx1, gy2), (gx2, gy2)]:
                self._side_canvas.create_line(cx_-tk_len, cy_, cx_+tk_len, cy_,
                                              fill="#f2c94c", width=1, tags="guide")
                self._side_canvas.create_line(cx_, cy_-tk_len, cx_, cy_+tk_len,
                                              fill="#f2c94c", width=1, tags="guide")
        self._side_info.config(text=info, fg=FG_GREEN)

    def _goto(self, step):
        for p in self._panels:
            p.hide()
        self._step = step
        self._panels[step].show()
        self._step_bar.set_step(step)
        self._back_btn.config(state="normal" if step > 0 else "disabled")
        last = len(self._panels) - 1
        if step == last:
            self._next_btn.config(text=_t("igw.restart"), command=lambda: self._goto(0))
        else:
            self._next_btn.config(text=_t("igw.next"), command=self._next)
        if step == 0:
            if hasattr(self, "_gen_log"):
                self._gen_log.clear()
            if hasattr(self, "_gen_prog"):
                self._gen_prog["value"] = 0
        # Always refresh side preview when switching steps
        self._schedule_preview()

    def _back(self): self._goto(max(0, self._step - 1))
    def _next(self): self._goto(min(len(self._panels) - 1, self._step + 1))

    # ── Colour helpers ────────────────────────────────────────────────────────
    def _swatch(self, parent, color_var):
        """Small coloured square that tracks a StringVar."""
        sw = tk.Frame(parent, width=26, height=26, relief="flat")
        sw.pack_propagate(False)
        try:
            sw.config(bg=color_var.get())
        except Exception:
            sw.config(bg="#888888")

        def _upd(*_):
            c = color_var.get()
            if c.startswith("#") and len(c) in (4, 7):
                try:
                    sw.config(bg=c)
                except Exception:
                    pass
        color_var.trace_add("write", _upd)
        return sw

    def _color_btn(self, parent, color_var, label_txt="🎨"):
        """Button that opens ColorPickerDialog and writes result into color_var."""
        sw = self._swatch(parent, color_var)

        def _open():
            dlg = ColorPickerDialog(self, initial=color_var.get())
            self.wait_window(dlg)
            if dlg.result:
                color_var.set(dlg.result)

        b = btn(parent, label_txt, _open, primary=False)
        return sw, b

    # ── Step 0 · Canvas ───────────────────────────────────────────────────────
    def _build_s0(self):
        p = WizardPanel(self._content)
        label(p, _t("s0.title"), font=FONT_H2).pack(anchor="w", pady=(0, 12))

        dim = styled_frame(p)
        dim.pack(anchor="w", pady=4)
        label(dim, _t("s0.width")).pack(side="left")
        tk.Entry(dim, textvariable=self._w_var, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=7
                 ).pack(side="left", padx=4)
        label(dim, _t("s0.height_px")).pack(side="left")
        tk.Entry(dim, textvariable=self._h_var, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=7
                 ).pack(side="left", padx=4)
        label(dim, "px").pack(side="left")

        def _sync_w(*_):
            if self._link_wh.get() and not self._syncing:
                self._syncing = True
                self._h_var.set(self._w_var.get())
                self._syncing = False

        def _sync_h(*_):
            if self._link_wh.get() and not self._syncing:
                self._syncing = True
                self._w_var.set(self._h_var.get())
                self._syncing = False

        self._w_var.trace_add("write", _sync_w)
        self._h_var.trace_add("write", _sync_h)

        tk.Checkbutton(p, text=_t("s0.link_wh"),
                       variable=self._link_wh,
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=(6, 12))

        separator(p).pack(fill="x", pady=(0, 10))
        label(p, _t("s0.presets"), fg=FG2, font=FONT_SM).pack(anchor="w")

        # ── Custom presets row
        presets_custom = [
            ("OPUS\n320×210",          320,  210),
            ("MIKRO\n96×128",           96,  128),
            ("KIKUBI\n1024×672",      1024,  672),
            ("KIKUBI List\n300×300",   300,  300),
        ]
        row1 = styled_frame(p)
        row1.pack(anchor="w", pady=(6, 2))
        label(row1, _t("s0.proj"), fg=FG2, font=FONT_SM).pack(side="left", padx=(0, 8))
        for lbl_txt, w, h in presets_custom:
            display = lbl_txt.replace("\n", "  ")
            def _apply_c(ww=w, hh=h):
                self._link_wh.set(False)
                self._w_var.set(str(ww))
                self._h_var.set(str(hh))
            btn(row1, display, _apply_c, primary=True).pack(side="left", padx=3)

        # ── Generic presets row
        presets_generic = [
            ("100×100", 100, 100), ("200×200", 200, 200), ("400×400", 400, 400),
            ("500×500", 500, 500), ("512×512", 512, 512), ("1024×1024", 1024, 1024),
            ("1280×720", 1280, 720), ("1920×1080", 1920, 1080),
        ]
        row2 = styled_frame(p)
        row2.pack(anchor="w", pady=2)
        label(row2, _t("s0.std"), fg=FG2, font=FONT_SM).pack(side="left", padx=(0, 4))
        for lbl_txt, w, h in presets_generic:
            def _apply(ww=w, hh=h):
                self._link_wh.set(False)
                self._w_var.set(str(ww))
                self._h_var.set(str(hh))
            btn(row2, lbl_txt, _apply, primary=False).pack(side="left", padx=3)

        separator(p).pack(fill="x", pady=(14, 10))
        anal_row = styled_frame(p)
        anal_row.pack(anchor="w", fill="x")
        btn(anal_row, _t("s0.analyze"), self._analyse_ref_image,
            primary=False).pack(side="left")
        label(anal_row, _t("s0.analyze_hint"),
              fg=FG2, font=FONT_SM).pack(side="left", padx=(10, 0))
        return p

    def _analyse_ref_image(self):
        """Load a reference image and extract canvas size, text colour,
        approximate font size, position, and drop-shadow into the wizard.
        Uses PIL-native operations (no pixel loops) for speed."""
        path = filedialog.askopenfilename(
            parent=self, title=_t("anal.dlg"),
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp"), ("Tous", "*.*")])
        if not path:
            return
        try:
            from PIL import Image as _Img, ImageFilter as _IFlt
            img_rgb = _Img.open(path).convert("RGB")
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self)
            return

        W, H = img_rgb.size
        self._link_wh.set(False)
        self._w_var.set(str(W))
        self._h_var.set(str(H))

        # ── Background colour: dominant colour in a 5% corner crop ──────────
        border = max(4, int(min(W, H) * 0.05))
        corner = img_rgb.crop((0, 0, border, border))
        corner_colors = corner.getcolors(maxcolors=border * border * 4)
        if corner_colors:
            bg_r, bg_g, bg_b = max(corner_colors, key=lambda x: x[0])[1]
        else:
            bg_r, bg_g, bg_b = 255, 255, 255
        bg_gray = int(0.299 * bg_r + 0.587 * bg_g + 0.114 * bg_b)
        dark_bg = bg_gray <= 128          # True → light text on dark bg

        # ── Binary mask: find non-background pixels via point() — no loops ──
        gray = img_rgb.convert("L")
        THRESH = 40
        if dark_bg:
            mask = gray.point(lambda p: 255 if p > bg_gray + THRESH else 0)
        else:
            mask = gray.point(lambda p: 255 if p < bg_gray - THRESH else 0)

        # Denoise (removes single-pixel noise)
        mask = mask.filter(_IFlt.MaxFilter(3)).filter(_IFlt.MinFilter(3))

        bbox = mask.getbbox()             # (left, top, right, bottom) | None
        if not bbox:
            messagebox.showinfo(_t("anal.title"), _t("anal.none"), parent=self)
            return

        left, top, right, bottom = bbox
        cx = (left + right) // 2
        cy = (top + bottom) // 2
        font_est = max(8, round((bottom - top) * 0.80))

        # ── Dominant text colour from the bounding-box crop ─────────────────
        crop = img_rgb.crop((left, top, right, bottom))
        crop_colors = crop.getcolors(maxcolors=W * H)
        text_color = "#ffffff" if dark_bg else "#000000"
        if crop_colors:
            non_bg = [
                (cnt, (r, g, b)) for cnt, (r, g, b) in crop_colors
                if abs(r - bg_r) + abs(g - bg_g) + abs(b - bg_b) > 50
            ]
            if non_bg:
                r, g, b = max(non_bg, key=lambda x: x[0])[1]
                text_color = f"#{r:02x}{g:02x}{b:02x}"

        # ── Shadow detection: look for a dark cluster offset from main bbox ─
        shadow_on, sh_ox, sh_oy, sh_col = False, 4, 4, "#000000"
        if dark_bg:
            # On dark backgrounds the "shadow" would be darker than the bg —
            # skip (shadows rarely visible on dark canvas)
            pass
        else:
            # Light bg: shadow pixels are darker than bg, separate from text
            shadow_mask = gray.point(lambda p: 255 if p < bg_gray - THRESH - 20 else 0)
            shadow_mask = shadow_mask.filter(_IFlt.MaxFilter(3)).filter(_IFlt.MinFilter(3))
            sbbox = shadow_mask.getbbox()
            if sbbox:
                sc_x = (sbbox[0] + sbbox[2]) // 2
                sc_y = (sbbox[1] + sbbox[3]) // 2
                off_x, off_y = sc_x - cx, sc_y - cy
                if abs(off_x) >= 2 or abs(off_y) >= 2:
                    shadow_on = True
                    sh_ox, sh_oy = off_x, off_y
                    scrop = img_rgb.crop((sbbox[0], sbbox[1], sbbox[2], sbbox[3]))
                    sc_cols = scrop.getcolors(maxcolors=W * H)
                    if sc_cols:
                        sr, sg, sb_ = max(sc_cols, key=lambda x: x[0])[1]
                        sh_col = f"#{sr:02x}{sg:02x}{sb_:02x}"

        # ── Apply to wizard ──────────────────────────────────────────────────
        self._pos_manual.set(True)
        self._pos_x.set(str(cx))
        self._pos_y.set(str(cy))
        self._fsize.set(str(font_est))
        self._auto_fsize.set(False)
        self._tcol.set(text_color)
        if shadow_on:
            self._shadow_on.set(True)
            self._shadow_ox.set(str(sh_ox))
            self._shadow_oy.set(str(sh_oy))
            self._shadow_col.set(sh_col)

        self._schedule_preview()

        lines = [
            f"Canvas     :  {W} × {H} px",
            f"Position   :  ({cx}, {cy})",
            f"Font size  :  ~{font_est} px",
            f"Text colour:  {text_color}",
        ]
        if shadow_on:
            lines.append(f"Shadow     :  offset ({sh_ox}, {sh_oy})  colour {sh_col}")
        messagebox.showinfo(_t("anal.title"), "\n".join(lines), parent=self)

    # ── Step 1 · Background ───────────────────────────────────────────────────
    # ── Preset helpers ────────────────────────────────────────────────────────
    def _preset_state(self) -> dict:
        """Collect all wizard settings into a serialisable dict."""
        return {
            "w": self._w_var.get(), "h": self._h_var.get(),
            "bg_mode": self._bg_mode.get(), "bg_color": self._bg_color.get(),
            "bg_img": self._bg_img.get(),
            "bg_pattern": list(self._bg_pattern),
            "num_list": self._num_list.get(),
            "font_family": self._font_family.get(), "font_style": self._font_style.get(),
            "fsize": self._fsize.get(), "tcol": self._tcol.get(),
            "auto_fsize": self._auto_fsize.get(),
            "shadow_on": self._shadow_on.get(), "shadow_col": self._shadow_col.get(),
            "shadow_ox": self._shadow_ox.get(), "shadow_oy": self._shadow_oy.get(),
            "shadow_blur": self._shadow_blur.get(),
            "pos_manual": self._pos_manual.get(),
            "pos_x": self._pos_x.get(), "pos_y": self._pos_y.get(),
            "corner": self._corner.get(), "radius": self._radius.get(),
            "fmt": self._fmt.get(), "quality": self._quality.get(),
            "prefix": self._prefix.get(), "suffix": self._suffix.get(),
            "out_folder": self._out_folder.get(),
            "guide_on": self._guide_on.get(), "guide_margin": self._guide_margin.get(),
            "tracking": self._tracking.get(),
        }

    def _apply_preset(self, state: dict):
        """Restore wizard settings from a dict."""
        def sv(var, key):
            if key in state: var.set(state[key])
        sv(self._w_var, "w");                sv(self._h_var, "h")
        sv(self._bg_mode, "bg_mode");        sv(self._bg_color, "bg_color")
        sv(self._bg_img, "bg_img")
        if "bg_pattern" in state:
            self._bg_pattern = list(state["bg_pattern"])
            self._rebuild_pat_rows()
        sv(self._num_list, "num_list")
        sv(self._font_family, "font_family"); sv(self._font_style, "font_style")
        sv(self._fsize, "fsize");             sv(self._tcol, "tcol")
        sv(self._shadow_col, "shadow_col")
        sv(self._shadow_ox, "shadow_ox");     sv(self._shadow_oy, "shadow_oy")
        sv(self._shadow_blur, "shadow_blur")
        sv(self._pos_x, "pos_x");            sv(self._pos_y, "pos_y")
        sv(self._corner, "corner");           sv(self._radius, "radius")
        sv(self._fmt, "fmt");                 sv(self._quality, "quality")
        sv(self._prefix, "prefix");           sv(self._suffix, "suffix")
        sv(self._out_folder, "out_folder")
        for bvar, key in [(self._auto_fsize, "auto_fsize"), (self._shadow_on, "shadow_on"),
                          (self._pos_manual, "pos_manual"), (self._guide_on, "guide_on"),
                          (self._link_wh, "link_wh")]:
            if key in state: bvar.set(bool(state[key]))
        if "guide_margin" in state:
            self._guide_margin.set(int(state["guide_margin"]))
        if "tracking" in state:
            self._tracking.set(int(state["tracking"]))
        self._update_font_path()
        self._update_preview()

    def _build_s1(self):
        p = WizardPanel(self._content)
        label(p, _t("s1.title"), font=FONT_H2).pack(anchor="w", pady=(0, 10))

        # Option 1 — solid colour
        opt1 = styled_frame(p)
        opt1.pack(anchor="w", fill="x", pady=2)
        tk.Radiobutton(opt1, text=_t("s1.color"), variable=self._bg_mode, value="color",
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY, command=self._toggle_bg).pack(anchor="w")
        self._color_panel = styled_frame(opt1)
        self._color_panel.pack(anchor="w", fill="x", padx=24, pady=(4, 6))
        cf = styled_frame(self._color_panel)
        cf.pack(anchor="w")
        label(cf, _t("s1.hex")).pack(side="left")
        tk.Entry(cf, textvariable=self._bg_color, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=10
                 ).pack(side="left", padx=6)
        sw, cb = self._color_btn(cf, self._bg_color)
        sw.pack(side="left", padx=(0, 2))
        cb.pack(side="left")

        pal_row = styled_frame(self._color_panel)
        pal_row.pack(anchor="w", pady=(6, 0))
        label(pal_row, _t("s1.quick"), fg=FG2, font=FONT_SM).pack(side="left", padx=(0, 6))
        palette = [("#1e1e2e","Noir bleuté"), ("#ffffff","Blanc"), ("#000000","Noir"),
                   ("#3a86ff","Bleu"), ("#ff6b6b","Rouge"), ("#06d6a0","Vert"),
                   ("#ffd166","Jaune"), ("#8338ec","Violet"), ("#f72585","Rose")]
        for hc, _ in palette:
            f = tk.Frame(pal_row, bg=hc, width=22, height=22, cursor="hand2")
            f.pack(side="left", padx=2)
            f.bind("<Button-1>", lambda e, c=hc: self._bg_color.set(c))

        # Option 2 — image file
        opt2 = styled_frame(p)
        opt2.pack(anchor="w", fill="x", pady=2)
        tk.Radiobutton(opt2, text=_t("s1.image"),
                       variable=self._bg_mode, value="image",
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY, command=self._toggle_bg).pack(anchor="w")
        self._img_panel = styled_frame(opt2)
        self._img_panel.pack(anchor="w", fill="x", padx=24, pady=(4, 0))
        ipf = styled_frame(self._img_panel)
        ipf.pack(anchor="w")
        btn(ipf, _t("s1.browse"), self._pick_bg_image, primary=False).pack(side="left")
        btn(ipf, _t("s1.paste_path"), self._paste_bg_path, primary=False).pack(side="left", padx=(6, 0))
        self._bg_img_lbl = label(ipf, _t("s1.no_file"), fg=FG2, font=FONT_SM)
        self._bg_img_lbl.pack(side="left")
        label(self._img_panel,
              _t("s1.hint"),
              fg=FG2, font=FONT_SM).pack(anchor="w", pady=(4, 0))

        # Drag-and-drop an image file directly onto this panel
        def _on_img_drop(paths):
            raw = paths[0]
            if Path(raw).is_file():
                self._bg_img.set(raw)
                self._bg_img_lbl.config(text=f"  {Path(raw).name}", fg=FG)
                self._bg_mode.set("image")
                self._toggle_bg()
        _enable_drop(self._img_panel, _on_img_drop)

        # Option 3 — pattern (multiple images that cycle)
        opt3 = styled_frame(p)
        opt3.pack(anchor="w", fill="x", pady=2)
        tk.Radiobutton(opt3, text=_t("s1.pattern"),
                       variable=self._bg_mode, value="pattern",
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY, command=self._toggle_bg).pack(anchor="w")
        self._pat_panel = styled_frame(opt3)
        self._pat_panel.pack(anchor="w", fill="x", padx=24, pady=(4, 0))
        self._pat_rows_frame = styled_frame(self._pat_panel)
        self._pat_rows_frame.pack(anchor="w", fill="x")
        self._pat_empty_lbl = label(self._pat_rows_frame, _t("s1.pat_empty"),
                                     fg=FG2, font=FONT_SM)
        self._pat_empty_lbl.pack(anchor="w", pady=2)
        pat_btns = styled_frame(self._pat_panel)
        pat_btns.pack(anchor="w", pady=(6, 0))
        btn(pat_btns, _t("s1.pat_add"), self._pick_pattern_images,
            primary=False).pack(side="left")
        self._pat_count_lbl = label(pat_btns, "", fg=FG2, font=FONT_SM)
        self._pat_count_lbl.pack(side="left", padx=(10, 0))
        label(self._pat_panel, _t("s1.pat_hint"),
              fg=FG2, font=FONT_SM).pack(anchor="w", pady=(6, 0))

        def _on_pat_drop(paths):
            for raw in paths:
                if Path(raw).is_file():
                    self._add_pattern_image(raw)
            self._bg_mode.set("pattern")
            self._toggle_bg()
        _enable_drop(self._pat_panel, _on_pat_drop)

        self._toggle_bg()
        return p

    def _toggle_bg(self):
        mode = self._bg_mode.get()
        if mode == "color":
            self._color_panel.pack(anchor="w", fill="x", padx=24, pady=(4, 6))
            self._img_panel.pack_forget()
            self._pat_panel.pack_forget()
        elif mode == "image":
            self._color_panel.pack_forget()
            self._img_panel.pack(anchor="w", fill="x", padx=24, pady=(4, 0))
            self._pat_panel.pack_forget()
        else:  # pattern
            self._color_panel.pack_forget()
            self._img_panel.pack_forget()
            self._pat_panel.pack(anchor="w", fill="x", padx=24, pady=(4, 0))

    def _pick_bg_image(self):
        path = filedialog.askopenfilename(
            parent=self, title=_t("s1.bg_dlg"),
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp"), ("Tous", "*.*")])
        if path:
            self._bg_img.set(path)
            self._bg_img_lbl.config(text=f"  {Path(path).name}", fg=FG)

    def _paste_bg_path(self):
        try:
            raw = self.clipboard_get().strip().strip('"').strip("'")
            if raw and Path(raw).is_file():
                self._bg_img.set(raw)
                self._bg_img_lbl.config(text=f"  {Path(raw).name}", fg=FG)
                self._bg_mode.set("image")
                self._toggle_bg()
        except Exception:
            pass

    # ── Pattern helpers ───────────────────────────────────────────────────────
    def _pick_pattern_images(self):
        """Open a multi-file picker and add selected images to the pattern."""
        paths = filedialog.askopenfilenames(
            parent=self, title=_t("s1.pat_add"),
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp"), ("Tous", "*.*")])
        for p in paths:
            self._add_pattern_image(p)

    def _add_pattern_image(self, path: str):
        if path and path not in self._bg_pattern:
            self._bg_pattern.append(path)
        self._rebuild_pat_rows()
        self._schedule_preview()

    def _remove_pattern_image(self, idx: int):
        if 0 <= idx < len(self._bg_pattern):
            self._bg_pattern.pop(idx)
        self._rebuild_pat_rows()
        self._schedule_preview()

    def _move_pattern_image(self, idx: int, direction: int):
        lst = self._bg_pattern
        new_idx = idx + direction
        if 0 <= new_idx < len(lst):
            lst[idx], lst[new_idx] = lst[new_idx], lst[idx]
        self._rebuild_pat_rows()

    def _rebuild_pat_rows(self):
        """Rebuild the pattern image list display."""
        for w in self._pat_rows_frame.winfo_children():
            w.destroy()
        if not self._bg_pattern:
            lbl = label(self._pat_rows_frame, _t("s1.pat_empty"), fg=FG2, font=FONT_SM)
            lbl.pack(anchor="w", pady=2)
        else:
            for i, path in enumerate(self._bg_pattern):
                row = styled_frame(self._pat_rows_frame)
                row.pack(anchor="w", fill="x", pady=1)
                label(row, f"{i+1}.", fg=FG2, font=FONT_SM, width=3).pack(side="left")
                name = Path(path).name
                disp = name if len(name) <= 32 else name[:29] + "…"
                label(row, f"🖼  {disp}", font=FONT_SM).pack(side="left", padx=(2, 6))
                btn(row, "↑", lambda ii=i: self._move_pattern_image(ii, -1),
                    primary=False).pack(side="left", padx=1)
                btn(row, "↓", lambda ii=i: self._move_pattern_image(ii, +1),
                    primary=False).pack(side="left", padx=1)
                btn(row, "✕", lambda ii=i: self._remove_pattern_image(ii),
                    primary=False).pack(side="left", padx=(1, 0))
        n = len(self._bg_pattern)
        self._pat_count_lbl.config(
            text=f"  {n} image{'s' if n != 1 else ''}" if n else "")

    # ── Step 2 · Numbers ──────────────────────────────────────────────────────
    def _build_s2(self):
        p = WizardPanel(self._content)

        # Scrollable inner canvas so step 2 never clips
        _canvas = tk.Canvas(p, bg=BG, highlightthickness=0)
        _sb = tk.Scrollbar(p, orient="vertical", command=_canvas.yview, bg=BG2)
        _canvas.config(yscrollcommand=_sb.set)
        inner = styled_frame(_canvas)
        inner.bind("<Configure>",
                   lambda e: _canvas.config(scrollregion=_canvas.bbox("all")))
        _canvas.create_window((0, 0), window=inner, anchor="nw")
        _canvas.pack(side="left", fill="both", expand=True)
        _sb.pack(side="right", fill="y")
        # Enable mouse-wheel on canvas + all children (bound after content is built)
        p.bind("<Map>", lambda e, c=_canvas: _enable_mousewheel_scroll(c), add="+")

        label(inner, _t("s2.style_title"), font=FONT_H2).pack(anchor="w", pady=(0, 8))

        # ── Number list ────────────────────────────────────────────────────────
        label(inner, _t("s2.nums_title"), font=FONT_H2).pack(anchor="w", pady=(0, 4))
        label(inner,
              _t("s2.nums_hint"),
              fg=FG2, font=FONT_SM).pack(anchor="w", pady=(0, 4))
        rng = styled_frame(inner)
        rng.pack(anchor="w", fill="x", pady=2)
        tk.Entry(rng, textvariable=self._num_list, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=44
                 ).pack(side="left", padx=(0, 8))
        self._range_lbl = label(rng, "", fg=FG_GREEN, font=FONT_SM)
        self._range_lbl.pack(side="left")

        def _upd_count(*_):
            nums = _parse_num_list(self._num_list.get())
            n = len(nums)
            suffix = _t("s2.count_range", a=nums[0], b=nums[-1]) if n > 1 else ""
            self._range_lbl.config(
                text=_t("s2.count", n=n) + suffix,
                fg=FG_GREEN if n > 0 else FG_RED)
        self._num_list.trace_add("write", _upd_count)
        _upd_count()

        separator(inner).pack(fill="x", pady=8)

        # ── Font ───────────────────────────────────────────────────────────────
        label(inner, _t("s2.font_title"), font=FONT_H2).pack(anchor="w", pady=(0, 6))

        fam_dict    = _get_font_families()
        all_families = list(fam_dict.keys())
        init_fam = "Impact" if "Impact" in fam_dict else (all_families[0] if all_families else "")
        self._font_family.set(init_fam)

        # — Family row —
        row_fam = styled_frame(inner)
        row_fam.pack(anchor="w", pady=2)
        label(row_fam, _t("s2.family")).pack(side="left")
        self._font_fam_cb = ttk.Combobox(row_fam, textvariable=self._font_family,
                                          values=all_families, state="normal", width=26,
                                          style="Custom.TCombobox", font=FONT_BODY)
        self._font_fam_cb.pack(side="left", padx=6)
        btn(row_fam, _t("s2.preview_btn"), self._open_font_preview,
            primary=False).pack(side="left", padx=4)

        def _filter_families(*_):
            q = self._font_family.get().lower()
            filtered = [n for n in all_families if q in n.lower()]
            self._font_fam_cb["values"] = filtered if filtered else all_families
            try:
                self._font_fam_cb.event_generate("<Down>")
            except Exception:
                pass
        self._font_fam_cb.bind("<KeyRelease>", lambda e: _filter_families())
        self._font_family.trace_add("write", lambda *_: self._refresh_style_cb())

        # — Style + Size row —
        row_sty = styled_frame(inner)
        row_sty.pack(anchor="w", pady=2)
        label(row_sty, _t("s2.style")).pack(side="left")
        self._font_sty_cb = ttk.Combobox(row_sty, textvariable=self._font_style,
                                          values=[], state="readonly", width=18,
                                          style="Custom.TCombobox", font=FONT_BODY)
        self._font_sty_cb.pack(side="left", padx=6)
        self._font_style.trace_add("write", lambda *_: self._update_font_path())
        label(row_sty, _t("s2.size")).pack(side="left")
        tk.Entry(row_sty, textvariable=self._fsize, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=6
                 ).pack(side="left", padx=4)
        label(row_sty, "px", fg=FG2, font=FONT_SM).pack(side="left")
        label(row_sty, _t("s2.tracking")).pack(side="left", padx=(16, 0))
        tk.Spinbox(row_sty, textvariable=self._tracking,
                   from_=-500, to=500, increment=1, width=5,
                   bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                   buttonbackground=BG3, font=FONT_BODY).pack(side="left", padx=4)
        label(row_sty, "px", fg=FG2, font=FONT_SM).pack(side="left")
        label(inner, _t("s2.tracking_hint"), fg=FG2, font=FONT_SM).pack(anchor="w", pady=(0, 2))

        # Initialise style list for default family
        self._refresh_style_cb()

        # — Taille mode toggle —
        mode_frm = tk.Frame(inner, bg=BG3, padx=6, pady=6)
        mode_frm.pack(anchor="w", pady=(10, 2), fill="x")
        label(mode_frm, _t("s2.mode_lbl"), bg=BG3).pack(side="left", padx=(0, 10))
        tk.Radiobutton(mode_frm, text=_t("s2.fixed"),
                       variable=self._auto_fsize, value=False,
                       bg=BG3, fg=FG, selectcolor=ACCENT, activebackground=BG3,
                       font=FONT_BODY).pack(side="left")
        tk.Radiobutton(mode_frm, text=_t("s2.auto"),
                       variable=self._auto_fsize, value=True,
                       bg=BG3, fg=FG, selectcolor=ACCENT, activebackground=BG3,
                       font=FONT_BODY).pack(side="left", padx=(12, 0))
        label(inner,
              _t("s2.auto_hint"),
              fg=FG2, font=FONT_SM).pack(anchor="w", pady=(2, 4))
        label(inner, _t("s2.tip"),
              fg=FG2, font=FONT_SM).pack(anchor="w", pady=(0, 4))

        separator(inner).pack(fill="x", pady=8)

        # ── Text colour ────────────────────────────────────────────────────────
        label(inner, _t("s2.color_title"), font=FONT_H2).pack(anchor="w", pady=(0, 6))
        row_c = styled_frame(inner)
        row_c.pack(anchor="w", pady=2)
        label(row_c, _t("s2.hex_lbl")).pack(side="left")
        tk.Entry(row_c, textvariable=self._tcol, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=10
                 ).pack(side="left", padx=6)
        sw_tc, btn_tc = self._color_btn(row_c, self._tcol)
        sw_tc.pack(side="left", padx=(0, 2))
        btn_tc.pack(side="left")
        tc_row = styled_frame(inner)
        tc_row.pack(anchor="w", pady=(4, 0))
        label(tc_row, _t("s2.quick_lbl"), fg=FG2, font=FONT_SM).pack(side="left", padx=(0, 6))
        for hc in ["#ffffff", "#000000", "#ffff00", "#ff4444", "#44ff99", "#66aaff", "#ff9900"]:
            f = tk.Frame(tc_row, bg=hc, width=22, height=22, cursor="hand2")
            f.pack(side="left", padx=2)
            f.bind("<Button-1>", lambda e, c=hc: self._tcol.set(c))

        separator(inner).pack(fill="x", pady=8)

        # ── Drop shadow ────────────────────────────────────────────────────────
        label(inner, _t("s2.shadow_title"), font=FONT_H2).pack(anchor="w", pady=(0, 6))
        sh_hdr = styled_frame(inner)
        sh_hdr.pack(anchor="w")
        tk.Checkbutton(sh_hdr, text=_t("s2.shadow_on"),
                       variable=self._shadow_on,
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY, command=self._toggle_shadow).pack(side="left")
        self._shadow_panel = styled_frame(inner)
        self._shadow_panel.pack(anchor="w", fill="x", padx=20, pady=(4, 0))

        sh_r1 = styled_frame(self._shadow_panel)
        sh_r1.pack(anchor="w", pady=2)
        label(sh_r1, _t("s2.shadow_col")).pack(side="left")
        tk.Entry(sh_r1, textvariable=self._shadow_col, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=10
                 ).pack(side="left", padx=6)
        sw_sh, btn_sh = self._color_btn(sh_r1, self._shadow_col)
        sw_sh.pack(side="left", padx=(0, 2))
        btn_sh.pack(side="left")
        for hc in ["#000000", "#222222", "#444444", "#001133", "#330000"]:
            f = tk.Frame(sh_r1, bg=hc, width=18, height=18, cursor="hand2")
            f.pack(side="left", padx=2)
            f.bind("<Button-1>", lambda e, c=hc: self._shadow_col.set(c))

        sh_r2 = styled_frame(self._shadow_panel)
        sh_r2.pack(anchor="w", pady=2)
        label(sh_r2, _t("s2.shadow_x")).pack(side="left")
        self._sh_ox_slider = tk.Scale(sh_r2, from_=-40, to=40, orient="horizontal",
                                       bg=BG2, fg=FG, highlightthickness=0,
                                       troughcolor=BG3, activebackground=ACCENT, length=140,
                                       command=lambda v: self._shadow_ox.set(str(int(float(v)))))
        self._sh_ox_slider.set(4)
        self._sh_ox_slider.pack(side="left", padx=6)
        tk.Entry(sh_r2, textvariable=self._shadow_ox, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=5
                 ).pack(side="left")
        label(sh_r2, _t("s2.shadow_y")).pack(side="left", padx=(8, 0))
        self._sh_oy_slider = tk.Scale(sh_r2, from_=-40, to=40, orient="horizontal",
                                       bg=BG2, fg=FG, highlightthickness=0,
                                       troughcolor=BG3, activebackground=ACCENT, length=140,
                                       command=lambda v: self._shadow_oy.set(str(int(float(v)))))
        self._sh_oy_slider.set(4)
        self._sh_oy_slider.pack(side="left", padx=6)
        tk.Entry(sh_r2, textvariable=self._shadow_oy, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=5
                 ).pack(side="left")
        label(sh_r2, "px", fg=FG2, font=FONT_SM).pack(side="left", padx=4)

        sh_r3 = styled_frame(self._shadow_panel)
        sh_r3.pack(anchor="w", pady=2)
        label(sh_r3, _t("s2.shadow_blur")).pack(side="left")
        self._sh_blur_slider = tk.Scale(sh_r3, from_=0, to=20, orient="horizontal",
                                         bg=BG2, fg=FG, highlightthickness=0,
                                         troughcolor=BG3, activebackground=ACCENT, length=160,
                                         command=lambda v: self._shadow_blur.set(str(int(float(v)))))
        self._sh_blur_slider.set(3)
        self._sh_blur_slider.pack(side="left", padx=6)
        tk.Entry(sh_r3, textvariable=self._shadow_blur, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=5
                 ).pack(side="left")
        label(sh_r3, "px", fg=FG2, font=FONT_SM).pack(side="left", padx=4)

        # Sync sliders from entries
        def _sync_sh_ox(*_):
            try: self._sh_ox_slider.set(int(self._shadow_ox.get()))
            except Exception: pass
        def _sync_sh_oy(*_):
            try: self._sh_oy_slider.set(int(self._shadow_oy.get()))
            except Exception: pass
        def _sync_sh_bl(*_):
            try: self._sh_blur_slider.set(int(self._shadow_blur.get()))
            except Exception: pass
        self._shadow_ox.trace_add("write", _sync_sh_ox)
        self._shadow_oy.trace_add("write", _sync_sh_oy)
        self._shadow_blur.trace_add("write", _sync_sh_bl)

        self._toggle_shadow()

        separator(inner).pack(fill="x", pady=8)

        # ── Manual position ────────────────────────────────────────────────────
        label(inner, _t("s2.pos_title"), font=FONT_H2).pack(anchor="w", pady=(0, 6))
        pos_hdr = styled_frame(inner)
        pos_hdr.pack(anchor="w")
        tk.Checkbutton(pos_hdr, text=_t("s2.pos_manual"),
                       variable=self._pos_manual,
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY, command=self._toggle_pos).pack(side="left")
        self._pos_panel = styled_frame(inner)
        self._pos_panel.pack(anchor="w", fill="x", padx=20, pady=(4, 0))
        label(self._pos_panel,
              _t("s2.pos_hint"),
              fg=FG2, font=FONT_SM).pack(anchor="w", pady=(0, 4))

        pos_r = styled_frame(self._pos_panel)
        pos_r.pack(anchor="w")
        label(pos_r, "X :").pack(side="left")
        # Guard flags prevent the slider command writing back while we're
        # syncing the slider from the entry (would cause clamping to slider max).
        _px_busy = [False]
        _py_busy = [False]

        def _on_px_slider(v):
            if not _px_busy[0]:
                self._pos_x.set(str(int(float(v))))

        def _on_py_slider(v):
            if not _py_busy[0]:
                self._pos_y.set(str(int(float(v))))

        self._px_slider = tk.Scale(pos_r, from_=0, to=2000, orient="horizontal",
                                    bg=BG2, fg=FG, highlightthickness=0,
                                    troughcolor=BG3, activebackground=ACCENT, length=180,
                                    command=_on_px_slider)
        self._px_slider.set(0)
        self._px_slider.pack(side="left", padx=6)

        # Entry: digits only; slider follows on Enter/FocusOut (no mid-typing clamping)
        def _pos_vcmd(new_val):
            return new_val == "" or (new_val.lstrip("-").isdigit())

        _px_vcmd = (self.register(lambda P: _pos_vcmd(P)), "%P")
        _py_vcmd = (self.register(lambda P: _pos_vcmd(P)), "%P")

        def _commit_px(e=None):
            raw = self._pos_x.get()
            v = int(raw) if raw.lstrip("-").isdigit() and raw not in ("", "-") else 0
            self._pos_x.set(str(v))
            _px_busy[0] = True
            try: self._px_slider.set(v)
            finally: _px_busy[0] = False

        def _commit_py(e=None):
            raw = self._pos_y.get()
            v = int(raw) if raw.lstrip("-").isdigit() and raw not in ("", "-") else 0
            self._pos_y.set(str(v))
            _py_busy[0] = True
            try: self._py_slider.set(v)
            finally: _py_busy[0] = False

        px_entry = tk.Entry(pos_r, textvariable=self._pos_x,
                            validate="key", validatecommand=_px_vcmd,
                            bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                            font=FONT_BODY, width=6)
        px_entry.pack(side="left")
        px_entry.bind("<Return>",   _commit_px)
        px_entry.bind("<FocusOut>", _commit_px)

        label(pos_r, _t("s2.pos_xy")).pack(side="left", padx=(8, 0))

        self._py_slider = tk.Scale(pos_r, from_=0, to=2000, orient="horizontal",
                                    bg=BG2, fg=FG, highlightthickness=0,
                                    troughcolor=BG3, activebackground=ACCENT, length=180,
                                    command=_on_py_slider)
        self._py_slider.set(0)
        self._py_slider.pack(side="left", padx=6)

        py_entry = tk.Entry(pos_r, textvariable=self._pos_y,
                            validate="key", validatecommand=_py_vcmd,
                            bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                            font=FONT_BODY, width=6)
        py_entry.pack(side="left")
        py_entry.bind("<Return>",   _commit_py)
        py_entry.bind("<FocusOut>", _commit_py)

        label(pos_r, "px", fg=FG2, font=FONT_SM).pack(side="left", padx=4)

        # Adapt slider max to canvas size live, clamping to guide zone if active
        def _adapt_sliders(*_):
            try:
                mx = max(10, int(self._w_var.get()))
                my = max(10, int(self._h_var.get()))
                if self._guide_on.get():
                    gm = self._guide_margin.get()
                    self._px_slider.config(from_=gm, to=max(gm+1, mx - gm))
                    self._py_slider.config(from_=gm, to=max(gm+1, my - gm))
                else:
                    self._px_slider.config(from_=0, to=mx)
                    self._py_slider.config(from_=0, to=my)
            except Exception:
                pass
        self._w_var.trace_add("write", _adapt_sliders)
        self._h_var.trace_add("write", _adapt_sliders)
        self._guide_on.trace_add("write", _adapt_sliders)
        self._guide_margin.trace_add("write", _adapt_sliders)

        self._toggle_pos()

        separator(inner).pack(fill="x", pady=8)

        # ── Guide mark ────────────────────────────────────────────────────────
        label(inner, _t("s2.guide_title"), font=FONT_H2).pack(anchor="w", pady=(0, 6))
        guide_hdr = styled_frame(inner)
        guide_hdr.pack(anchor="w", fill="x")
        tk.Checkbutton(guide_hdr, text=_t("s2.guide_on"),
                       variable=self._guide_on,
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY, command=self._toggle_guide).pack(side="left")
        self._guide_panel = styled_frame(inner)
        self._guide_panel.pack(anchor="w", fill="x", padx=20, pady=(4, 0))
        gm_row = styled_frame(self._guide_panel)
        gm_row.pack(anchor="w", pady=2)
        label(gm_row, _t("s2.guide_margin")).pack(side="left")
        self._guide_slider = tk.Scale(gm_row, from_=1, to=200,
                                      variable=self._guide_margin,
                                      orient="horizontal", bg=BG2, fg=FG,
                                      highlightthickness=0, troughcolor=BG3,
                                      activebackground=ACCENT, length=180)
        self._guide_slider.pack(side="left", padx=6)

        # Entry: digits-only, clamps to [1..200] on Return / focus-out
        _gm_str = tk.StringVar(value=str(self._guide_margin.get()))

        def _gm_str_to_int(*_):
            """StringVar → IntVar (fires when user types)."""
            raw = _gm_str.get()
            if raw.isdigit() and raw != "":
                v = max(1, min(200, int(raw)))
                if self._guide_margin.get() != v:
                    self._guide_margin.set(v)

        def _gm_int_to_str(*_):
            """IntVar → StringVar (fires when slider moves or OPUS btn clicked)."""
            new_val = str(self._guide_margin.get())
            if _gm_str.get() != new_val:
                _gm_str.set(new_val)

        def _gm_clamp_entry(e=None):
            """On Enter / focus-out: clamp and normalise the entry display."""
            raw = _gm_str.get()
            v   = max(1, min(200, int(raw))) if raw.isdigit() and raw != "" else self._guide_margin.get()
            self._guide_margin.set(v)
            _gm_str.set(str(v))

        def _gm_validate(action, new_val):
            """Allow only digit strings (or empty during editing)."""
            return new_val == "" or new_val.isdigit()

        vcmd = (self.register(_gm_validate), "%d", "%P")
        _gm_str.trace_add("write", _gm_str_to_int)
        self._guide_margin.trace_add("write", _gm_int_to_str)

        gm_entry = tk.Entry(gm_row, textvariable=_gm_str,
                            validate="key", validatecommand=vcmd,
                            bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                            font=FONT_BODY, width=5)
        gm_entry.pack(side="left", padx=4)
        gm_entry.bind("<Return>",    _gm_clamp_entry)
        gm_entry.bind("<FocusOut>",  _gm_clamp_entry)

        label(gm_row, "px", fg=FG2, font=FONT_SM).pack(side="left")
        btn(gm_row, _t("s2.guide_opus"),
            lambda: self._guide_margin.set(40),
            primary=False).pack(side="left", padx=(10, 0))
        label(self._guide_panel, _t("s2.guide_hint"),
              fg=FG2, font=FONT_SM).pack(anchor="w", pady=(4, 0))
        self._toggle_guide()
        return p

    def _toggle_shadow(self):
        if self._shadow_on.get():
            self._shadow_panel.pack(anchor="w", fill="x", padx=20, pady=(4, 0))
        else:
            self._shadow_panel.pack_forget()

    def _toggle_pos(self):
        if self._pos_manual.get():
            self._pos_panel.pack(anchor="w", fill="x", padx=20, pady=(4, 0))
        else:
            self._pos_panel.pack_forget()

    def _toggle_guide(self):
        if self._guide_on.get():
            self._guide_panel.pack(anchor="w", fill="x", padx=20, pady=(4, 0))
        else:
            self._guide_panel.pack_forget()

    # ── Step 3 · Corners ──────────────────────────────────────────────────────
    def _build_s3(self):
        p = WizardPanel(self._content)
        label(p, _t("s3.title"), font=FONT_H2).pack(anchor="w", pady=(0, 12))

        # Both options in their own container so toggle doesn't reorder rows
        opt1 = styled_frame(p)
        opt1.pack(anchor="w", fill="x", pady=2)
        tk.Radiobutton(opt1, text=_t("s3.sharp"),
                       variable=self._corner, value="sharp",
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY, command=self._toggle_corners).pack(anchor="w")

        opt2 = styled_frame(p)
        opt2.pack(anchor="w", fill="x", pady=2)
        tk.Radiobutton(opt2, text=_t("s3.rounded"),
                       variable=self._corner, value="rounded",
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY, command=self._toggle_corners).pack(anchor="w")
        self._radius_frm = styled_frame(opt2)
        self._radius_frm.pack(anchor="w", fill="x", padx=24, pady=(4, 0))
        label(self._radius_frm, _t("s3.radius")).pack(side="left")
        self._radius_slider = tk.Scale(self._radius_frm, from_=2, to=250,
                                       orient="horizontal", bg=BG2, fg=FG,
                                       highlightthickness=0, troughcolor=BG3,
                                       activebackground=ACCENT, length=260,
                                       command=lambda v: self._radius.set(str(int(float(v)))))
        self._radius_slider.set(40)
        self._radius_slider.pack(side="left", padx=8)
        tk.Entry(self._radius_frm, textvariable=self._radius,
                 bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                 font=FONT_BODY, width=5).pack(side="left", padx=4)
        label(self._radius_frm, "px", fg=FG2, font=FONT_SM).pack(side="left")

        def _sync_slider(*_):
            try:
                self._radius_slider.set(int(self._radius.get()))
            except Exception:
                pass
        self._radius.trace_add("write", _sync_slider)

        separator(p).pack(fill="x", pady=12)
        label(p, _t("s3.warn_jpeg"),
              fg=FG_YELLOW, font=FONT_SM).pack(anchor="w")
        label(p, _t("s3.hint_png"),
              fg=FG2, font=FONT_SM).pack(anchor="w", pady=(2, 0))

        self._toggle_corners()
        return p

    def _toggle_corners(self):
        if self._corner.get() == "rounded":
            self._radius_frm.pack(anchor="w", fill="x", padx=24, pady=(4, 0))
        else:
            self._radius_frm.pack_forget()

    # ── Step 4 · Output ───────────────────────────────────────────────────────
    def _build_s4(self):
        p = WizardPanel(self._content)
        label(p, _t("s4.title"), font=FONT_H2).pack(anchor="w", pady=(0, 10))

        fmt_row = styled_frame(p)
        fmt_row.pack(anchor="w")
        for fmt in ("PNG", "JPEG"):
            tk.Radiobutton(fmt_row, text=fmt, variable=self._fmt, value=fmt,
                           bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                           font=FONT_BODY, command=self._toggle_fmt
                           ).pack(side="left", padx=(0, 16))

        self._qual_frm = styled_frame(p)
        self._qual_frm.pack(anchor="w", fill="x", padx=24, pady=(6, 0))
        label(self._qual_frm, _t("s4.quality")).pack(side="left")
        tk.Scale(self._qual_frm, from_=60, to=100, variable=self._quality,
                 orient="horizontal", bg=BG2, fg=FG, highlightthickness=0,
                 troughcolor=BG3, activebackground=ACCENT, length=200
                 ).pack(side="left", padx=8)
        label(self._qual_frm, "%", fg=FG2, font=FONT_SM).pack(side="left")

        separator(p).pack(fill="x", pady=12)
        label(p, _t("s4.naming"), font=FONT_H2).pack(anchor="w", pady=(0, 8))
        row_pfx = styled_frame(p)
        row_pfx.pack(anchor="w", pady=4)
        label(row_pfx, _t("s4.prefix")).pack(side="left")
        tk.Entry(row_pfx, textvariable=self._prefix, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=16
                 ).pack(side="left", padx=6)
        label(row_pfx, _t("s4.suffix")).pack(side="left", padx=(12, 0))
        tk.Entry(row_pfx, textvariable=self._suffix, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=14
                 ).pack(side="left", padx=6)
        self._naming_ex = label(row_pfx, "", fg=FG2, font=FONT_SM)
        self._naming_ex.pack(side="left", padx=6)

        def _upd_ex(*_):
            ext = ".png" if self._fmt.get() == "PNG" else ".jpg"
            nums = _parse_num_list(self._num_list.get())
            n = nums[0] if nums else 1
            sfx = self._suffix.get()
            self._naming_ex.config(text=f"ex: {self._prefix.get()}{n}{sfx}{ext}")
        self._prefix.trace_add("write", _upd_ex)
        self._suffix.trace_add("write", _upd_ex)
        self._fmt.trace_add("write", _upd_ex)
        self._num_list.trace_add("write", _upd_ex)
        _upd_ex()

        separator(p).pack(fill="x", pady=12)
        label(p, _t("s4.folder"), font=FONT_H2).pack(anchor="w", pady=(0, 8))
        frow = styled_frame(p)
        frow.pack(anchor="w", fill="x")
        btn(frow, _t("s4.choose"), self._pick_out_folder, primary=False).pack(side="left")
        self._out_lbl = label(frow, f"  {self._out_folder.get()}", fg=FG2, font=FONT_SM,
                              wraplength=540, justify="left")
        self._out_lbl.pack(side="left", padx=6, fill="x")

        self._toggle_fmt()

        separator(p).pack(fill="x", pady=12)

        # ── Presets ───────────────────────────────────────────────────────────
        label(p, _t("preset.title"), font=FONT_H2).pack(anchor="w", pady=(0, 6))
        preset_row = styled_frame(p)
        preset_row.pack(anchor="w", fill="x")

        # Combobox listing saved presets
        self._preset_var = tk.StringVar()
        self._preset_cb  = ttk.Combobox(preset_row, textvariable=self._preset_var,
                                         state="readonly", width=22,
                                         style="Custom.TCombobox", font=FONT_BODY)
        self._preset_cb.pack(side="left", padx=(0, 8))
        self._refresh_preset_list()

        btn(preset_row, _t("preset.load"),
            self._load_preset, primary=False).pack(side="left", padx=(0, 6))
        btn(preset_row, _t("preset.save"),
            self._save_preset, primary=True).pack(side="left", padx=(0, 6))
        btn(preset_row, _t("preset.delete"),
            self._delete_preset, primary=False).pack(side="left")

        return p

    def _toggle_fmt(self):
        if self._fmt.get() == "JPEG":
            self._qual_frm.pack(anchor="w", fill="x", padx=24, pady=(6, 0))
        else:
            self._qual_frm.pack_forget()

    def _pick_out_folder(self):
        path = filedialog.askdirectory(parent=self, title=_t("s4.dlg_title"),
                                       initialdir=self._out_folder.get())
        if path:
            self._out_folder.set(path)
            self._out_lbl.config(text=f"  {path}", fg=FG)

    def _refresh_preset_list(self):
        presets = _load_presets()
        names = list(presets.keys()) or [_t("preset.none")]
        self._preset_cb["values"] = names
        if names:
            self._preset_cb.set(names[0])

    def _save_preset(self):
        # Ask for a name
        name = self._ask_simple_text(_t("preset.name_dlg"), default=self._preset_var.get())
        if not name or name == _t("preset.none"):
            return
        presets = _load_presets()
        presets[name] = self._preset_state()
        _save_presets(presets)
        self._refresh_preset_list()
        self._preset_var.set(name)
        self._gen_log.log(_t("preset.saved", name=name), "ok") if hasattr(self, "_gen_log") else None

    def _load_preset(self):
        name = self._preset_var.get()
        presets = _load_presets()
        if name not in presets:
            return
        self._apply_preset(presets[name])
        self._gen_log.log(_t("preset.loaded", name=name), "ok") if hasattr(self, "_gen_log") else None

    def _delete_preset(self):
        name = self._preset_var.get()
        presets = _load_presets()
        if name not in presets:
            return
        if not messagebox.askyesno(_t("preset.delete"),
                                   _t("preset.confirm_del", name=name), parent=self):
            return
        del presets[name]
        _save_presets(presets)
        self._refresh_preset_list()

    def _ask_simple_text(self, prompt: str, default: str = "") -> str:
        """Small inline dialog that returns a string (or '' if cancelled)."""
        out = [""]
        dlg = tk.Toplevel(self)
        dlg.title(prompt)
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()
        label(dlg, prompt, font=FONT_BODY).pack(padx=20, pady=(16, 4), anchor="w")
        var = tk.StringVar(value=default)
        e   = tk.Entry(dlg, textvariable=var, bg=BG3, fg=FG, insertbackground=FG,
                       relief="flat", font=FONT_BODY, width=28)
        e.pack(padx=20, pady=(0, 8))
        e.select_range(0, "end")
        e.focus_set()
        row = styled_frame(dlg)
        row.pack(fill="x", padx=20, pady=(0, 12))
        def _ok(e=None):
            out[0] = var.get().strip()
            dlg.destroy()
        def _cancel(e=None):
            dlg.destroy()
        btn(row, "OK", _ok, primary=True).pack(side="right", padx=(6, 0))
        btn(row, "Cancel", _cancel, primary=False).pack(side="right")
        dlg.bind("<Return>", _ok)
        dlg.bind("<Escape>", _cancel)
        dlg.update_idletasks()
        pw, py = self.winfo_rootx(), self.winfo_rooty()
        dlg.geometry(f"360x130+{pw+80}+{py+80}")
        self.wait_window(dlg)
        return out[0]

    # ── Step 5 · Preview & Generate ───────────────────────────────────────────
    def _build_s5(self):
        p = WizardPanel(self._content)
        outer = styled_frame(p)
        outer.pack(fill="both", expand=True)

        # The right side preview panel is always visible — no separate left preview needed here
        right = styled_frame(outer)
        right.pack(fill="both", expand=True)

        label(right, _t("s5.summary"), font=FONT_H2).pack(anchor="w", pady=(0, 6))
        self._summary_lbl = label(right, "", fg=FG2, font=FONT_SM, justify="left")
        self._summary_lbl.pack(anchor="w")

        separator(right).pack(fill="x", pady=8)

        btn_row = styled_frame(right)
        btn_row.pack(anchor="w", pady=(0, 6))
        btn(btn_row, _t("s5.generate"), self._execute, primary=True).pack(side="left")
        btn(btn_row, _t("s5.refresh"),
            self._schedule_preview, primary=False).pack(side="left", padx=8)
        self._open_btn = btn(btn_row, _t("s5.open_folder"),
                             lambda: os.startfile(str(self._out_folder.get())),
                             primary=False)
        self._open_btn.config(state="disabled")
        self._open_btn.pack(side="left", padx=8)
        self._undo_btn = btn(btn_row, _t("undo.btn"),
                             self._undo_generation, primary=False)
        self._undo_btn.config(state="disabled")
        self._undo_btn.pack(side="left", padx=8)

        self._gen_prog = ttk.Progressbar(right, orient="horizontal",
                                          length=360, mode="determinate")
        self._gen_prog.pack(anchor="w", pady=(0, 6))
        self._gen_log = LogPanel(right)
        self._gen_log.pack(fill="both", expand=True)

        # Thumbnail grid — shown after generation
        self._thumb_frame = styled_frame(right)
        label(self._thumb_frame, _t("s5.grid_title"), font=FONT_H2).pack(anchor="w", pady=(0, 4))
        self._thumb_inner = styled_frame(self._thumb_frame)
        self._thumb_inner.pack(anchor="w")
        self._thumb_frame.pack_forget()   # hidden until generation finishes
        return p

    def _refresh_style_cb(self, *_):
        """Update the style combobox when the family changes."""
        fams   = _get_font_families()
        fam    = self._font_family.get()
        styles = sorted(fams.get(fam, {}).keys())
        if not styles:
            styles = ["Regular"]
        if hasattr(self, '_font_sty_cb'):
            self._font_sty_cb["values"] = styles
        cur = self._font_style.get()
        if cur not in styles:
            self._font_style.set("Regular" if "Regular" in styles else styles[0])
        self._update_font_path()

    def _update_font_path(self, *_):
        """Resolve (family, style) → absolute font path and store in _font_path."""
        fams  = _get_font_families()
        fam   = self._font_family.get()
        sty   = self._font_style.get()
        styles = fams.get(fam, {})
        path  = styles.get(sty) or (next(iter(styles.values())) if styles else "")
        self._font_path.set(path)
        display = fam if sty in ('Regular', '') else f"{fam} {sty}"
        self._font_var.set(display)   # keep display name in sync

    def _open_font_preview(self):
        """Open FontPreviewDialog and apply the selected font (family + style)."""
        dlg = FontPreviewDialog(self, current_font=self._font_family.get())
        self.wait_window(dlg)
        if not dlg.result:
            return
        flat = dlg.result   # e.g. "Arial Bold" or "Arial"
        fams = _get_font_families()
        found = False
        for fam, styles in fams.items():
            for sty, _ in styles.items():
                key = fam if sty == 'Regular' else f"{fam} {sty}"
                if key == flat:
                    self._font_family.set(fam)
                    self._refresh_style_cb()
                    self._font_style.set(sty)
                    self._update_font_path()
                    found = True
                    break
            if found:
                break
        if not found and flat in fams:   # family name typed directly
            self._font_family.set(flat)
            self._refresh_style_cb()

    def _update_preview(self):
        """Update the step-6 summary text and trigger the side preview."""
        nums = _parse_num_list(self._num_list.get())
        n  = len(nums)
        nf = nums[0] if nums else 1
        nt = nums[-1] if nums else 1
        corners_txt = (_t("s5.rounded_r", r=self._radius.get()) if self._corner.get() == "rounded"
                       else _t("s5.sharp"))
        bg_txt = ("Image" if self._bg_mode.get() == "image"
                  else self._bg_color.get())
        auto_txt = _t("s5.auto_tag") if self._auto_fsize.get() else ""
        if hasattr(self, "_summary_lbl"):
            self._summary_lbl.config(
                text="\n".join([
                    _t("s5.sum_canvas", w=self._w_var.get(), h=self._h_var.get()),
                    _t("s5.sum_nums", nums=self._num_list.get(), n=n),
                    _t("s5.sum_font", fam=self._font_family.get(),
                       sty=self._font_style.get(), sz=self._fsize.get(), auto=auto_txt),
                    _t("s5.sum_bg", bg=bg_txt),
                    _t("s5.sum_corners", c=corners_txt),
                    _t("s5.sum_fmt", fmt=self._fmt.get(), pfx=self._prefix.get(),
                       sfx=self._suffix.get()),
                    _t("s5.sum_out", out=self._out_folder.get()),
                ]))
        self._schedule_preview(delay_ms=0)

    # ── Execute ───────────────────────────────────────────────────────────────
    def _execute(self):
        nums = _parse_num_list(self._num_list.get())
        if not nums:
            messagebox.showerror(_t("err.empty_list"), _t("err.empty_msg"), parent=self)
            return
        try:
            w   = int(self._w_var.get())
            h   = int(self._h_var.get())
            fsz = int(self._fsize.get())
            rad = int(self._radius.get())
        except ValueError as exc:
            messagebox.showerror(_t("err.bad_value"), str(exc), parent=self)
            return
        if self._bg_mode.get() == "image" and not self._bg_img.get():
            messagebox.showerror(_t("err.no_bg"), _t("err.no_bg_msg"), parent=self)
            return
        if self._bg_mode.get() == "pattern" and not self._bg_pattern:
            messagebox.showerror(_t("err.no_pat"), _t("err.no_pat_msg"), parent=self)
            return

        out_dir = Path(self._out_folder.get())
        out_dir.mkdir(parents=True, exist_ok=True)

        total = len(nums)
        self._gen_prog["maximum"] = total
        self._gen_prog["value"]   = 0
        self._gen_log.clear()
        self._next_btn.config(state="disabled")
        self._back_btn.config(state="disabled")

        fmt      = self._fmt.get()
        ext      = ".png" if fmt == "PNG" else ".jpg"
        prefix   = self._prefix.get()
        suffix   = self._suffix.get()
        quality  = self._quality.get()
        bg_mode  = self._bg_mode.get()
        bg_col   = self._bg_color.get()
        bg_img   = self._bg_img.get()
        tcol     = self._tcol.get()
        fname    = self._font_path.get() or self._font_var.get()
        corner   = self._corner.get()
        sh_on    = self._shadow_on.get()
        sh_col   = self._shadow_col.get()
        sh_ox    = int(self._shadow_ox.get() or 0)
        sh_oy    = int(self._shadow_oy.get() or 0)
        sh_bl    = int(self._shadow_blur.get() or 0)
        p_man    = self._pos_manual.get()
        p_x      = int(self._pos_x.get() or 0)
        p_y      = int(self._pos_y.get() or 0)
        auto_fit = self._auto_fsize.get()
        tracking = self._tracking.get()

        bg_pattern = list(self._bg_pattern)  # snapshot for thread

        def _worker():
            ok = 0
            generated = []
            for idx, number in enumerate(nums, 1):
                # Resolve bg for this specific image
                cur_bg_mode = bg_mode
                cur_bg_img  = bg_img
                if bg_mode == "pattern" and bg_pattern:
                    cur_bg_mode = "image"
                    cur_bg_img  = bg_pattern[(idx - 1) % len(bg_pattern)]
                try:
                    img = _gen_image(number=number, width=w, height=h,
                                     bg_mode=cur_bg_mode, bg_color=bg_col,
                                     bg_image_path=cur_bg_img, text_color=tcol,
                                     font_name=fname, font_size=fsz,
                                     corner_mode=corner, corner_radius=rad,
                                     shadow_on=sh_on, shadow_color=sh_col,
                                     shadow_offset_x=sh_ox, shadow_offset_y=sh_oy,
                                     shadow_blur=sh_bl,
                                     pos_manual=p_man, pos_x=p_x, pos_y=p_y,
                                     auto_fit=auto_fit, letter_spacing=tracking)
                    dest = out_dir / f"{prefix}{number}{suffix}{ext}"
                    if fmt == "JPEG":
                        from PIL import Image as _Img
                        flat = _Img.new("RGBA", img.size, (255, 255, 255, 255))
                        if img.mode == "RGBA":
                            flat.paste(img, mask=img.split()[3])
                        else:
                            flat.paste(img)
                        flat.convert("RGB").save(str(dest), "JPEG", quality=quality)
                    else:
                        img.save(str(dest), "PNG")
                    generated.append(dest)
                    ok += 1
                    done = idx
                    self.after(0, lambda d=done, m=f"✔  {dest.name}": (
                        self._gen_log.log(m, "ok"),
                        self._gen_prog.__setitem__("value", d)
                    ))
                except Exception as exc:
                    self.after(0, lambda n=number, e=str(exc):
                               self._gen_log.log(f"✘  #{n}  {e}", "err"))
            self.after(0, lambda: self._on_done(ok, total, out_dir, generated))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_done(self, ok, total, out_dir, generated=None):
        self._last_gen_files = generated or []
        self._next_btn.config(state="normal")
        self._back_btn.config(state="normal")
        self._gen_log.log(_t("err.log_sum", ok=ok, total=total, out=out_dir), "dim")
        self._open_btn.config(state="normal")
        self._undo_btn.config(state="normal" if self._last_gen_files else "disabled")
        nums = _parse_num_list(self._num_list.get())
        if nums and ok > 0:
            threading.Thread(
                target=lambda: self._show_thumb_grid(nums, out_dir),
                daemon=True).start()
        if ok == total:
            messagebox.showinfo(_t("err.done"), _t("err.done_msg", ok=ok, out=out_dir), parent=self)

    def _undo_generation(self):
        if not self._last_gen_files:
            messagebox.showinfo(_t("undo.btn"), _t("undo.empty"), parent=self)
            return
        n = len(self._last_gen_files)
        if not messagebox.askyesno(_t("undo.btn"),
                                   _t("undo.confirm", n=n), parent=self):
            return
        deleted = 0
        for p in self._last_gen_files:
            try:
                if p.exists():
                    p.unlink()
                    deleted += 1
            except Exception:
                pass
        self._last_gen_files.clear()
        self._undo_btn.config(state="disabled")
        self._gen_log.log(_t("undo.done", n=deleted), "dim")
        # Also hide the thumbnail grid
        self._thumb_frame.pack_forget()

    def _show_thumb_grid(self, nums, out_dir):
        """Load up to 9 evenly-spaced generated images and build the thumbnail grid."""
        from PIL import Image as _PilImg
        from PIL.ImageTk import PhotoImage as _PILPhoto
        ext     = ".png" if self._fmt.get() == "PNG" else ".jpg"
        pfx     = self._prefix.get()
        sfx     = self._suffix.get()
        THUMB   = 72

        # Pick ≤9 indices evenly spread
        step    = max(1, len(nums) // 9)
        samples = nums[::step][:9]

        photos = []
        for n in samples:
            path = out_dir / f"{pfx}{n}{sfx}{ext}"
            if path.exists():
                try:
                    img = _PilImg.open(str(path)).convert("RGBA")
                    img.thumbnail((THUMB, THUMB), _PilImg.LANCZOS)
                    photos.append((n, _PILPhoto(img)))
                except Exception:
                    pass

        self.after(0, lambda: self._place_thumb_grid(photos))

    def _place_thumb_grid(self, photos):
        """Render the thumbnail grid onto _thumb_inner and reveal _thumb_frame."""
        for w in self._thumb_inner.winfo_children():
            w.destroy()
        self._thumb_photos = [ph for _, ph in photos]   # keep refs alive
        COLS = 3
        for i, (n, ph) in enumerate(photos):
            r, c = divmod(i, COLS)
            cell = styled_frame(self._thumb_inner)
            cell.grid(row=r, column=c, padx=3, pady=3)
            tk.Label(cell, image=ph, bg=BG, relief="flat").pack()
            label(cell, str(n), fg=FG2, font=FONT_SM).pack()
        if photos:
            self._thumb_frame.pack(fill="x", pady=(8, 0))


# =============================================================================
#  CLIPBOARD MANAGER  —  persistent smart clipboard
# =============================================================================
CLIPBOARD_FILE = Path(os.path.abspath(__file__)).parent / "_clipboard_data.json"
PRESETS_FILE   = Path(os.path.abspath(__file__)).parent / "_igw_presets.json"

def _load_presets() -> dict:
    if PRESETS_FILE.exists():
        try:
            with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_presets(data: dict):
    with open(PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_clipboard_data() -> dict:
    if CLIPBOARD_FILE.exists():
        try:
            with open(CLIPBOARD_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"categories": [{"name": "Général", "items": []}]}

def save_clipboard_data(data: dict):
    with open(CLIPBOARD_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class ItemDialog(tk.Toplevel):
    """Small dialog to create or edit a clipboard snippet (label + body text)."""

    def __init__(self, parent, title="Nouvel élément", label_val="", text_val=""):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        self.result = None  # (label, text) or None if cancelled

        self._build(label_val, text_val)
        self.update_idletasks()
        # Centre on parent
        pw = parent.winfo_rootx()
        py = parent.winfo_rooty()
        w, h = 460, 340
        self.geometry(f"{w}x{h}+{pw + 60}+{py + 60}")

    def _build(self, label_val, text_val):
        pad = dict(padx=20, pady=8)

        label(self, "Étiquette (nom court)", font=FONT_SM, fg=FG2).pack(anchor="w", **pad)
        self._lbl_var = tk.StringVar(value=label_val)
        lbl_entry = tk.Entry(self, textvariable=self._lbl_var,
                             bg=BG3, fg=FG, insertbackground=FG,
                             relief="flat", font=FONT_BODY)
        lbl_entry.pack(fill="x", padx=20)
        lbl_entry.focus_set()

        label(self, "Contenu du snippet", font=FONT_SM, fg=FG2).pack(anchor="w", padx=20, pady=(12, 4))
        frm = styled_frame(self, bg=BG3)
        frm.pack(fill="both", expand=True, padx=20)
        self._txt = tk.Text(frm, bg=BG3, fg=FG, insertbackground=FG,
                            relief="flat", font=FONT_MONO, wrap="word",
                            width=50, height=8)
        sb = ttk.Scrollbar(frm, orient="vertical", command=self._txt.yview)
        self._txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._txt.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        self._txt.insert("1.0", text_val)

        sep = tk.Frame(self, bg=BG3, height=1)
        sep.pack(fill="x", padx=20, pady=(10, 0))

        row = styled_frame(self)
        row.pack(fill="x", padx=20, pady=10)
        btn(row, "Cancel", self._cancel, primary=False).pack(side="right", padx=(6, 0))
        btn(row, "✔  Save", self._save).pack(side="right")

        self.bind("<Escape>", lambda e: self._cancel())

    def _save(self):
        lbl = self._lbl_var.get().strip()
        txt = self._txt.get("1.0", "end-1c")
        if not lbl:
            messagebox.showwarning("Empty field", "Please enter a label.", parent=self)
            return
        if not txt:
            messagebox.showwarning("Empty field", "Content cannot be empty.", parent=self)
            return
        self.result = (lbl, txt)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class ClipboardManager(tk.Toplevel):
    """Persistent smart clipboard — categories + snippets, copy in one click."""

    WIN_W, WIN_H = 820, 560

    def __init__(self, parent):
        super().__init__(parent)
        self.title("📋  Presse-papier intelligent")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(660, 420)

        self._data = load_clipboard_data()
        self._cat_idx = 0       # currently selected category index
        self._item_idx = None   # currently selected item index (or None)
        self._flash_id = None   # after() id for status flash

        self._build()
        self._refresh_cats()
        self.update_idletasks()
        pw, py = parent.winfo_rootx(), parent.winfo_rooty()
        self.geometry(f"{self.WIN_W}x{self.WIN_H}+{pw+40}+{py+40}")

    # ── Layout ────────────────────────────────────────────────────────────────
    def _build(self):
        # Header
        hdr = styled_frame(self)
        hdr.pack(fill="x", padx=20, pady=(16, 4))
        label(hdr, "📋  Presse-papier intelligent", font=FONT_H1).pack(side="left")

        # Search bar
        sf = styled_frame(self)
        sf.pack(fill="x", padx=20, pady=(0, 8))
        label(sf, "🔍", font=FONT_BODY, fg=FG2).pack(side="left", padx=(0, 6))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._on_search())
        self._search_entry = tk.Entry(sf, textvariable=self._search_var,
                                      bg=BG3, fg=FG, insertbackground=FG,
                                      relief="flat", font=FONT_BODY)
        self._search_entry.pack(fill="x", expand=True, ipady=4)
        btn(sf, "✕", lambda: self._search_var.set(""),
            primary=False).pack(side="right", padx=(6, 0))

        separator(self).pack(fill="x", padx=20, pady=(0, 10))

        # Main split panel
        main = styled_frame(self)
        main.pack(fill="both", expand=True, padx=20, pady=(0, 0))

        # ── Left panel: categories ────────────────────────────────────────────
        left = styled_frame(main, bg=BG2)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)
        left.config(width=190)

        label(left, "Catégories", font=FONT_H2, bg=BG2).pack(anchor="w", padx=10, pady=(10, 4))

        cat_frm = tk.Frame(left, bg=BG2)
        cat_frm.pack(fill="both", expand=True, padx=6)
        sb_c = ttk.Scrollbar(cat_frm, orient="vertical")
        self._cat_lb = tk.Listbox(cat_frm, bg=BG3, fg=FG, selectbackground=ACCENT,
                                  selectforeground=FG, relief="flat",
                                  activestyle="none", font=FONT_BODY,
                                  yscrollcommand=sb_c.set, exportselection=False)
        sb_c.config(command=self._cat_lb.yview)
        sb_c.pack(side="right", fill="y")
        self._cat_lb.pack(fill="both", expand=True)
        self._cat_lb.bind("<<ListboxSelect>>", self._on_cat_select)

        cat_btns = styled_frame(left, bg=BG2)
        cat_btns.pack(fill="x", padx=6, pady=6)
        btn(cat_btns, "+ Nouvelle",  self._cat_new,    primary=True ).pack(side="left", padx=(0,3))
        btn(cat_btns, "✏",           self._cat_rename, primary=False).pack(side="left", padx=3)
        btn(cat_btns, "🗑",           self._cat_delete, primary=False).pack(side="left", padx=3)

        # ── Right panel: items + preview ──────────────────────────────────────
        right = styled_frame(main)
        right.pack(side="left", fill="both", expand=True)

        # Items listbox (top half)
        items_lbl = styled_frame(right)
        items_lbl.pack(fill="x")
        label(items_lbl, "Éléments", font=FONT_H2).pack(side="left", pady=(0, 4))

        items_frm = tk.Frame(right, bg=BG2, height=160)
        items_frm.pack(fill="x")
        items_frm.pack_propagate(False)
        isb = ttk.Scrollbar(items_frm, orient="vertical")
        self._item_lb = tk.Listbox(items_frm, bg=BG3, fg=FG, selectbackground=ACCENT,
                                   selectforeground=FG, relief="flat",
                                   activestyle="none", font=FONT_BODY,
                                   yscrollcommand=isb.set, exportselection=False)
        isb.config(command=self._item_lb.yview)
        isb.pack(side="right", fill="y")
        self._item_lb.pack(fill="both", expand=True)
        self._item_lb.bind("<<ListboxSelect>>", self._on_item_select)
        self._item_lb.bind("<Double-Button-1>", lambda e: self._copy_item())
        self._item_lb.bind("<Return>",           lambda e: self._copy_item())

        # Item action buttons
        ibtns = styled_frame(right)
        ibtns.pack(fill="x", pady=(4, 0))
        btn(ibtns, "📋  Copier",     self._copy_item,   primary=True ).pack(side="left", padx=(0, 4))
        btn(ibtns, "+ Nouveau",       self._item_new,    primary=False).pack(side="left", padx=4)
        btn(ibtns, "✏  Modifier",    self._item_edit,   primary=False).pack(side="left", padx=4)
        btn(ibtns, "↑",              self._item_up,     primary=False).pack(side="left", padx=2)
        btn(ibtns, "↓",              self._item_down,   primary=False).pack(side="left", padx=2)
        btn(ibtns, "🗑",              self._item_delete, primary=False).pack(side="left", padx=4)

        separator(right).pack(fill="x", pady=8)

        # Content preview
        label(right, "Content preview", font=FONT_SM, fg=FG2).pack(anchor="w")
        preview_frm = tk.Frame(right, bg=BG3)
        preview_frm.pack(fill="both", expand=True, pady=(4, 0))
        psb = ttk.Scrollbar(preview_frm, orient="vertical")
        self._preview = tk.Text(preview_frm, bg=BG3, fg=FG, font=FONT_MONO,
                                relief="flat", state="disabled", wrap="word",
                                yscrollcommand=psb.set)
        psb.config(command=self._preview.yview)
        psb.pack(side="right", fill="y")
        self._preview.pack(fill="both", expand=True, padx=4, pady=4)

        # Status bar
        self._status_var = tk.StringVar(value="")
        self._status = label(self, textvariable=self._status_var,
                             fg=FG_GREEN, font=FONT_SM, anchor="w")
        self._status.pack(fill="x", padx=20, pady=(6, 10))

    # ── Data helpers ──────────────────────────────────────────────────────────
    @property
    def _cats(self):
        return self._data["categories"]

    def _current_cat(self):
        if 0 <= self._cat_idx < len(self._cats):
            return self._cats[self._cat_idx]
        return None

    def _current_items(self):
        cat = self._current_cat()
        return cat["items"] if cat else []

    def _save(self):
        save_clipboard_data(self._data)

    # ── Refresh helpers ───────────────────────────────────────────────────────
    def _refresh_cats(self, keep_sel=True):
        prev = self._cat_idx
        self._cat_lb.delete(0, "end")
        for c in self._cats:
            n = len(c["items"])
            self._cat_lb.insert("end", f"  {c['name']}  ({n})")
        if self._cats:
            idx = min(prev, len(self._cats) - 1)
            self._cat_idx = idx
            self._cat_lb.selection_set(idx)
            self._cat_lb.see(idx)
        self._refresh_items()

    def _refresh_items(self, keep_sel=True):
        prev = self._item_idx
        self._item_lb.delete(0, "end")
        for it in self._current_items():
            self._item_lb.insert("end", f"  {it['label']}")
        if self._current_items():
            idx = 0 if prev is None else min(prev, len(self._current_items()) - 1)
            self._item_idx = idx
            self._item_lb.selection_set(idx)
            self._item_lb.see(idx)
        else:
            self._item_idx = None
        self._refresh_preview()

    def _refresh_preview(self):
        self._preview.config(state="normal")
        self._preview.delete("1.0", "end")
        items = self._current_items()
        if self._item_idx is not None and 0 <= self._item_idx < len(items):
            self._preview.insert("1.0", items[self._item_idx]["text"])
        self._preview.config(state="disabled")

    # ── Event handlers ────────────────────────────────────────────────────────
    def _on_cat_select(self, _event=None):
        sel = self._cat_lb.curselection()
        if sel:
            self._cat_idx = sel[0]
            self._item_idx = None
            self._refresh_items()

    def _on_item_select(self, _event=None):
        sel = self._item_lb.curselection()
        if sel:
            self._item_idx = sel[0]
            self._refresh_preview()

    def _on_search(self):
        query = self._search_var.get().strip().lower()
        self._item_lb.delete(0, "end")
        if not query:
            self._refresh_items()
            return
        # Search across all categories, show flat results
        results = []
        for cat in self._cats:
            for it in cat["items"]:
                if query in it["label"].lower() or query in it["text"].lower():
                    results.append((cat["name"], it))
        for cat_name, it in results:
            self._item_lb.insert("end", f"  [{cat_name}]  {it['label']}")
        # Store results for copy
        self._search_results = results

    def _flash(self, msg, color=FG_GREEN):
        if self._flash_id:
            self.after_cancel(self._flash_id)
        self._status_var.set(msg)
        self._status.config(fg=color)
        self._flash_id = self.after(2500, lambda: self._status_var.set(""))

    # ── Copy ──────────────────────────────────────────────────────────────────
    def _copy_item(self):
        query = self._search_var.get().strip()
        sel = self._item_lb.curselection()
        if not sel:
            return
        idx = sel[0]
        if query:
            if not hasattr(self, "_search_results") or idx >= len(self._search_results):
                return
            _, it = self._search_results[idx]
        else:
            items = self._current_items()
            if idx >= len(items):
                return
            it = items[idx]
        self.clipboard_clear()
        self.clipboard_append(it["text"])
        self._flash(f"✔  Copié : {it['label']}")

    # ── Category CRUD ─────────────────────────────────────────────────────────
    def _cat_new(self):
        name = self._ask_string("Nouvelle catégorie", "Nom de la catégorie :")
        if not name:
            return
        self._cats.append({"name": name, "items": []})
        self._cat_idx = len(self._cats) - 1
        self._save()
        self._refresh_cats()

    def _cat_rename(self):
        cat = self._current_cat()
        if not cat:
            return
        name = self._ask_string("Renommer", "Nouveau nom :", default=cat["name"])
        if not name:
            return
        cat["name"] = name
        self._save()
        self._refresh_cats()

    def _cat_delete(self):
        cat = self._current_cat()
        if not cat:
            return
        if not messagebox.askyesno("Supprimer",
                                   f"Supprimer la catégorie « {cat['name']} » et tous ses éléments ?",
                                   parent=self):
            return
        self._cats.pop(self._cat_idx)
        if not self._cats:
            self._cats.append({"name": "Général", "items": []})
        self._cat_idx = max(0, self._cat_idx - 1)
        self._save()
        self._refresh_cats()

    # ── Item CRUD ─────────────────────────────────────────────────────────────
    def _item_new(self):
        if not self._current_cat():
            return
        dlg = ItemDialog(self, title="Nouvel élément")
        self.wait_window(dlg)
        if dlg.result is None:
            return
        lbl, txt = dlg.result
        self._current_items().append({"label": lbl, "text": txt})
        self._item_idx = len(self._current_items()) - 1
        self._save()
        self._refresh_cats()

    def _item_edit(self):
        items = self._current_items()
        if self._item_idx is None or self._item_idx >= len(items):
            return
        it = items[self._item_idx]
        dlg = ItemDialog(self, title="Modifier l'élément",
                         label_val=it["label"], text_val=it["text"])
        self.wait_window(dlg)
        if dlg.result is None:
            return
        lbl, txt = dlg.result
        it["label"] = lbl
        it["text"] = txt
        self._save()
        self._refresh_items()

    def _item_delete(self):
        items = self._current_items()
        if self._item_idx is None or self._item_idx >= len(items):
            return
        it = items[self._item_idx]
        if not messagebox.askyesno("Supprimer",
                                   f"Supprimer « {it['label']} » ?", parent=self):
            return
        items.pop(self._item_idx)
        self._item_idx = max(0, self._item_idx - 1) if items else None
        self._save()
        self._refresh_cats()

    def _item_up(self):
        items = self._current_items()
        if self._item_idx is None or self._item_idx <= 0:
            return
        i = self._item_idx
        items[i], items[i - 1] = items[i - 1], items[i]
        self._item_idx -= 1
        self._save()
        self._refresh_items()

    def _item_down(self):
        items = self._current_items()
        if self._item_idx is None or self._item_idx >= len(items) - 1:
            return
        i = self._item_idx
        items[i], items[i + 1] = items[i + 1], items[i]
        self._item_idx += 1
        self._save()
        self._refresh_items()

    # ── Simple ask-string helper (avoids simpledialog import) ─────────────────
    def _ask_string(self, title, prompt, default=""):
        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()
        result = tk.StringVar(value=default)

        label(dlg, prompt, font=FONT_BODY).pack(padx=20, pady=(16, 4), anchor="w")
        e = tk.Entry(dlg, textvariable=result, bg=BG3, fg=FG,
                     insertbackground=FG, relief="flat", font=FONT_BODY, width=36)
        e.pack(padx=20, fill="x")
        e.select_range(0, "end")
        e.focus_set()

        out = [None]

        def confirm():
            v = result.get().strip()
            if v:
                out[0] = v
            dlg.destroy()

        row = styled_frame(dlg)
        row.pack(fill="x", padx=20, pady=12)
        btn(row, "Annuler", dlg.destroy, primary=False).pack(side="right", padx=(6, 0))
        btn(row, "OK", confirm).pack(side="right")
        dlg.bind("<Return>", lambda e: confirm())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.update_idletasks()
        pw, py = self.winfo_rootx(), self.winfo_rooty()
        w, h = 360, 140
        dlg.geometry(f"{w}x{h}+{pw+80}+{py+80}")
        self.wait_window(dlg)
        return out[0]


# =============================================================================
#  TOOL: CONVERT
# =============================================================================
class ConvertWizard(tk.Toplevel):
    _FMT_EXT = {"JPEG": ".jpg", "PNG": ".png", "WebP": ".webp", "BMP": ".bmp"}

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Image Tool  —  Convert")
        self.configure(bg=BG)
        self.geometry("920x680")
        self.resizable(True, True)
        self._folder = None
        self._files  = []
        self._build()

    def _build(self):
        hdr = styled_frame(self)
        hdr.pack(fill="x", padx=20, pady=(16, 0))
        label(hdr, "🔄  " + _t("conv.title"), font=FONT_H1).pack(side="left")
        separator(self).pack(fill="x", padx=20, pady=10)

        body = styled_frame(self)
        body.pack(fill="both", expand=True, padx=24, pady=(0, 8))

        # ── Folder browser ────────────────────────────────────────────────────
        label(body, "Source folder", font=FONT_H2).pack(anchor="w", pady=(0, 6))
        self._browser = FolderBrowser(body, next_cb=self._load_files_ui)
        self._browser.pack(anchor="w", fill="x")

        separator(body).pack(fill="x", pady=10)

        # ── Format selectors ─────────────────────────────────────────────────
        fmt_row = styled_frame(body)
        fmt_row.pack(anchor="w", fill="x", pady=4)

        label(fmt_row, _t("conv.src_fmt")).pack(side="left")
        self._src_fmt = ttk.Combobox(fmt_row, values=["ALL", "JPEG", "PNG", "WebP", "BMP"],
                                      state="readonly", width=10,
                                      style="Custom.TCombobox", font=FONT_BODY)
        self._src_fmt.set("ALL")
        self._src_fmt.pack(side="left", padx=(6, 20))
        self._src_fmt.bind("<<ComboboxSelected>>", lambda e: self._load_files_ui())

        label(fmt_row, _t("conv.dst_fmt")).pack(side="left")
        self._dst_fmt = ttk.Combobox(fmt_row, values=["JPEG", "PNG", "WebP", "BMP"],
                                      state="readonly", width=10,
                                      style="Custom.TCombobox", font=FONT_BODY)
        self._dst_fmt.set("PNG")
        self._dst_fmt.pack(side="left", padx=6)
        self._dst_fmt.bind("<<ComboboxSelected>>", self._toggle_quality)

        # JPEG quality (shown only when target is JPEG)
        self._qual_row = styled_frame(body)
        self._qual_row.pack(anchor="w", fill="x", pady=2)
        label(self._qual_row, _t("conv.quality")).pack(side="left")
        self._quality = tk.IntVar(value=92)
        tk.Scale(self._qual_row, from_=60, to=100, variable=self._quality,
                 orient="horizontal", bg=BG2, fg=FG, highlightthickness=0,
                 troughcolor=BG3, activebackground=ACCENT, length=180
                 ).pack(side="left", padx=6)
        label(self._qual_row, "%", fg=FG2, font=FONT_SM).pack(side="left")
        self._toggle_quality()

        separator(body).pack(fill="x", pady=10)

        # ── Output options ────────────────────────────────────────────────────
        self._overwrite = tk.BooleanVar(value=False)
        tk.Checkbutton(body, text=_t("conv.overwrite"),
                       variable=self._overwrite,
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY).pack(anchor="w")
        self._sub_folder = tk.BooleanVar(value=True)
        tk.Checkbutton(body, text=_t("conv.out_sub"),
                       variable=self._sub_folder,
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY).pack(anchor="w", pady=(4, 0))

        separator(body).pack(fill="x", pady=10)

        # ── File list + progress ──────────────────────────────────────────────
        self._file_count_lbl = label(body, "", fg=FG2, font=FONT_SM)
        self._file_count_lbl.pack(anchor="w", pady=(0, 4))
        self._prog = ttk.Progressbar(body, orient="horizontal",
                                     length=460, mode="determinate")
        self._prog.pack(anchor="w", pady=(0, 6))
        self._log  = LogPanel(body)
        self._log.pack(fill="both", expand=True)

        separator(self).pack(fill="x", padx=20, pady=6)
        nav = styled_frame(self)
        nav.pack(fill="x", padx=24, pady=(0, 16))
        self._go_btn = btn(nav, _t("conv.go"), self._execute, primary=True)
        self._go_btn.pack(side="right")

    def _toggle_quality(self, e=None):
        if self._dst_fmt.get() == "JPEG":
            self._qual_row.pack(anchor="w", fill="x", pady=2)
        else:
            self._qual_row.pack_forget()

    def _load_files_ui(self):
        folder = self._browser.get()
        if not folder:
            return
        self._folder = Path(folder)
        src = self._src_fmt.get()
        if src == "ALL":
            exts = ["*.jpg", "*.jpeg", "*.png", "*.webp", "*.bmp"]
        else:
            ext_map = {"JPEG": ["*.jpg", "*.jpeg"], "PNG": ["*.png"],
                       "WebP": ["*.webp"], "BMP": ["*.bmp"]}
            exts = ext_map.get(src, ["*.*"])
        self._files = collect_files(self._folder, exts, recurse=False)
        n = len(self._files)
        self._file_count_lbl.config(text=f"{n} file(s) found")

    def _execute(self):
        if not self._files:
            self._load_files_ui()
        if not self._files:
            messagebox.showwarning("No files", "No matching files found.", parent=self)
            return

        dst_name = self._dst_fmt.get()
        dst_ext  = self._FMT_EXT[dst_name]
        pil_fmt  = dst_name if dst_name != "JPEG" else "JPEG"
        quality  = self._quality.get()
        overwrite = self._overwrite.get()
        use_sub   = self._sub_folder.get()

        files = list(self._files)
        total = len(files)
        self._prog["maximum"] = total
        self._prog["value"]   = 0
        self._log.clear()
        self._go_btn.config(state="disabled")

        def _worker():
            ok = 0
            for idx, src_path in enumerate(files, 1):
                try:
                    from PIL import Image as _Img
                    img = _Img.open(str(src_path))

                    if pil_fmt == "JPEG":
                        if img.mode in ("RGBA", "P"):
                            bg = _Img.new("RGB", img.size, (255, 255, 255))
                            if img.mode == "RGBA":
                                bg.paste(img, mask=img.split()[3])
                            else:
                                bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[3])
                            img = bg
                        elif img.mode != "RGB":
                            img = img.convert("RGB")

                    if overwrite:
                        out_path = src_path.with_suffix(dst_ext)
                    elif use_sub:
                        out_dir  = src_path.parent / "converted"
                        out_dir.mkdir(exist_ok=True)
                        out_path = out_dir / src_path.with_suffix(dst_ext).name
                    else:
                        out_path = src_path.with_suffix(dst_ext)

                    save_kw = {"quality": quality, "optimize": True} if pil_fmt == "JPEG" else {}
                    img.save(str(out_path), pil_fmt, **save_kw)
                    ok += 1
                    self.after(0, lambda d=idx, m=f"✔  {src_path.name}  →  {out_path.name}": (
                        self._log.log(m, "ok"),
                        self._prog.__setitem__("value", d)))
                except Exception as exc:
                    self.after(0, lambda n=src_path.name, e=str(exc):
                               self._log.log(f"✘  {n}  {e}", "err"))
            self.after(0, lambda: self._on_done(ok, total))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_done(self, ok, total):
        self._go_btn.config(state="normal")
        self._log.log(_t("conv.done", ok=ok, total=total), "dim")
        if ok == total:
            messagebox.showinfo("Done ✔", _t("conv.done", ok=ok, total=total), parent=self)


# =============================================================================
#  GUIDE MARK WIZARD
# =============================================================================
class GuideMarkWizard(tk.Toplevel):
    """3-step print wizard.

    Step 0 · Design   — pick template image(s) from disk
    Step 1 · Configure — number range, font settings, live tile preview
    Step 2 · Print     — paper / layout preview, export / print

    Tiles = your template image resized to tile dimensions + number overlaid.
    No PIL drawing — just your own files used as backgrounds.
    """

    _TPL_CFG = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "_guidemark_templates.json")
    _PRESET_CFG = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "_guidemark_presets.json")

    PAPER_MM = {
        "A3ノビ  (483×329 mm)": (483, 329),
        "A3  (420×297 mm)":     (420, 297),
        "A4  (297×210 mm)":     (297, 210),
    }

    def __init__(self, master):
        super().__init__(master)
        self.title(_t("gmw.title"))
        self.configure(bg=BG)
        self.geometry("980x660")
        self.minsize(780, 520)
        self.resizable(True, True)

        self._step = 0
        self._selected_tpl: dict = {}
        self._templates: list = self._load_templates()

        # ── State vars ────────────────────────────────────────────────────
        self._num_range_str = tk.StringVar(value="1-15")
        self._paper         = tk.StringVar(value=list(self.PAPER_MM)[0])
        self._orient        = tk.StringVar(value="landscape")
        self._tile_w        = tk.DoubleVar(value=100.0)
        self._tile_h        = tk.DoubleVar(value=100.0)
        self._margin        = tk.DoubleVar(value=10.0)
        self._gap           = tk.DoubleVar(value=5.0)
        self._dpi           = tk.IntVar(value=300)
        self._out_folder    = tk.StringVar()
        self._printer       = tk.StringVar(value="")
        self._printers: list = []
        self._monochrome    = tk.BooleanVar(value=True)
        self._num_font_fam  = tk.StringVar(value="Futura Std")
        self._num_font_sty  = tk.StringVar(value="Medium")
        self._num_auto      = tk.BooleanVar(value=True)
        self._num_fsize_pt  = tk.IntVar(value=120)   # 1-2 digit manual pt
        self._num_fsize3_pt = tk.IntVar(value=90)    # 3+ digit manual/auto cap pt
        self._num_color     = tk.StringVar(value="#000000")
        self._num_pos_x     = tk.IntVar(value=50)   # % of tile width
        self._num_pos_y     = tk.IntVar(value=42)   # % of tile height
        self._preview_num   = tk.IntVar(value=1)
        # ── Display helpers (driven by traces after _build) ────────────────
        self._pos_x_pct_str = tk.StringVar(value="50%")
        self._pos_y_pct_str = tk.StringVar(value="42%")
        self._pos_x_px_str  = tk.StringVar(value="")
        self._pos_y_px_str  = tk.StringVar(value="")
        self._tile_px_str   = tk.StringVar(value="")

        self._sched_tile_id   = None
        self._sched_layout_id = None
        self._tile_tk   = None
        self._layout_tk = None
        self._layout_info     = tk.StringVar(value="—")
        self._layout_pages:   list = []
        self._layout_page_idx = tk.IntVar(value=0)
        self._layout_page_str = tk.StringVar(value="—")
        self._cut_line_show   = tk.BooleanVar(value=True)
        self._cut_line_pt     = tk.DoubleVar(value=0.2)
        self._preset_name     = tk.StringVar(value="")
        self._presets: list   = self._load_presets()
        self._thumb_cache: dict = {}

        self._build()
        self._refresh_cards()
        self._load_printers_async()

        for v in (self._monochrome,
                  self._num_font_fam, self._num_font_sty, self._num_auto,
                  self._num_fsize_pt, self._num_fsize3_pt, self._num_color,
                  self._num_pos_x, self._num_pos_y,
                  self._tile_w, self._tile_h, self._preview_num):
            v.trace_add("write", lambda *_: self._schedule_tile_preview())

        for v in (self._num_range_str, self._paper, self._orient,
                  self._margin, self._gap, self._dpi,
                  self._cut_line_show, self._cut_line_pt):
            v.trace_add("write", lambda *_: self._schedule_layout_preview())

        def _upd_px_displays(*_):
            if not self.winfo_exists():
                return
            dpi   = max(1, self._dpi.get())
            tw_px = max(1, round(self._tile_w.get() / 25.4 * dpi))
            th_px = max(1, round(self._tile_h.get() / 25.4 * dpi))
            self._tile_px_str.set(f"= {tw_px} × {th_px} px")
            self._pos_x_pct_str.set(f"{self._num_pos_x.get()}%")
            self._pos_y_pct_str.set(f"{self._num_pos_y.get()}%")
            self._pos_x_px_str.set(
                f"≈ {round(self._num_pos_x.get() / 100 * tw_px)} px")
            self._pos_y_px_str.set(
                f"≈ {round(self._num_pos_y.get() / 100 * th_px)} px")
        for v in (self._tile_w, self._tile_h, self._dpi,
                  self._num_pos_x, self._num_pos_y):
            v.trace_add("write", _upd_px_displays)
        _upd_px_displays()

    # ═════════════════════════════════════════════════════════════════════
    # Template persistence
    # ═════════════════════════════════════════════════════════════════════
    def _load_templates(self) -> list:
        try:
            with open(self._TPL_CFG, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [t for t in data
                    if isinstance(t, dict) and "path" in t
                    and os.path.isfile(t["path"])]
        except Exception:
            return []

    def _save_templates(self):
        try:
            with open(self._TPL_CFG, "w", encoding="utf-8") as f:
                json.dump(self._templates, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ═════════════════════════════════════════════════════════════════════
    # Preset persistence
    # ═════════════════════════════════════════════════════════════════════
    def _load_presets(self) -> list:
        try:
            with open(self._PRESET_CFG, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save_presets(self):
        try:
            with open(self._PRESET_CFG, "w", encoding="utf-8") as f:
                json.dump(self._presets, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _preset_data(self) -> dict:
        return {
            "name":          self._preset_name.get(),
            "num_range":     self._num_range_str.get(),
            "tile_w":        self._tile_w.get(),
            "tile_h":        self._tile_h.get(),
            "margin":        self._margin.get(),
            "gap":           self._gap.get(),
            "dpi":           self._dpi.get(),
            "paper":         self._paper.get(),
            "orient":        self._orient.get(),
            "monochrome":    self._monochrome.get(),
            "num_font_fam":  self._num_font_fam.get(),
            "num_font_sty":  self._num_font_sty.get(),
            "num_auto":      self._num_auto.get(),
            "num_fsize_pt":  self._num_fsize_pt.get(),
            "num_fsize3_pt": self._num_fsize3_pt.get(),
            "num_color":     self._num_color.get(),
            "num_pos_x":     self._num_pos_x.get(),
            "num_pos_y":     self._num_pos_y.get(),
            "cut_line_show": self._cut_line_show.get(),
            "cut_line_pt":   self._cut_line_pt.get(),
            "template_path": self._selected_tpl.get("path", ""),
        }

    def _apply_preset(self, d: dict):
        def _s(var, key): var.set(d[key]) if key in d else None
        _s(self._num_range_str, "num_range")
        _s(self._tile_w,        "tile_w")
        _s(self._tile_h,        "tile_h")
        _s(self._margin,        "margin")
        _s(self._gap,           "gap")
        _s(self._dpi,           "dpi")
        _s(self._paper,         "paper")
        _s(self._orient,        "orient")
        _s(self._monochrome,    "monochrome")
        _s(self._num_font_fam,  "num_font_fam")
        _s(self._num_font_sty,  "num_font_sty")
        _s(self._num_auto,      "num_auto")
        _s(self._num_fsize_pt,  "num_fsize_pt")
        _s(self._num_fsize3_pt, "num_fsize3_pt")
        _s(self._num_color,     "num_color")
        _s(self._num_pos_x,     "num_pos_x")
        _s(self._num_pos_y,     "num_pos_y")
        _s(self._cut_line_show, "cut_line_show")
        _s(self._cut_line_pt,   "cut_line_pt")
        tp = d.get("template_path", "")
        if tp:
            match = next((t for t in self._templates
                          if t.get("path") == tp), None)
            if match:
                self._selected_tpl = match
                self._refresh_cards()
        self._toggle_num_spin()

    def _save_preset_as(self):
        name = self._preset_name.get().strip()
        if not name:
            messagebox.showwarning("Preset",
                "Enter a preset name in the field first.", parent=self)
            return
        d = self._preset_data()
        d["name"] = name
        for i, p in enumerate(self._presets):
            if p.get("name") == name:
                self._presets[i] = d
                break
        else:
            self._presets.append(d)
        self._save_presets()
        if hasattr(self, "_preset_cb"):
            self._preset_cb["values"] = [p["name"] for p in self._presets]
        messagebox.showinfo("Preset", f'Preset "{name}" saved.', parent=self)

    def _load_selected_preset(self):
        name = self._preset_name.get().strip()
        d = next((p for p in self._presets if p.get("name") == name), None)
        if d:
            self._apply_preset(d)
        else:
            messagebox.showwarning("Preset",
                f'No preset named "{name}" found.', parent=self)

    def _delete_preset(self):
        name = self._preset_name.get().strip()
        self._presets = [p for p in self._presets if p.get("name") != name]
        self._save_presets()
        self._preset_name.set("")
        if hasattr(self, "_preset_cb"):
            self._preset_cb["values"] = [p["name"] for p in self._presets]

    # ═════════════════════════════════════════════════════════════════════
    # Build
    # ═════════════════════════════════════════════════════════════════════
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ── Step nav bar ──────────────────────────────────────────────────
        nav = tk.Frame(self, bg=BG2)
        nav.grid(row=0, column=0, sticky="ew")
        self._step_num_lbl: list = []
        self._step_txt_lbl: list = []
        for i, key in enumerate(("gmw.step_design",
                                  "gmw.step_configure",
                                  "gmw.step_print")):
            cell = tk.Frame(nav, bg=BG2)
            cell.pack(side="left", padx=20, pady=9)
            nl = tk.Label(cell, text=f"  {i+1}  ", bg=BG2, fg=FG2,
                          font=("Segoe UI", 9, "bold"))
            nl.pack(side="left")
            tl = tk.Label(cell, text=_t(key), bg=BG2, fg=FG2,
                          font=("Segoe UI", 9))
            tl.pack(side="left")
            self._step_num_lbl.append(nl)
            self._step_txt_lbl.append(tl)
            if i < 2:
                tk.Label(nav, text=" › ", bg=BG2, fg=FG2,
                         font=("Segoe UI", 11)).pack(side="left")

        # ── Content frames ────────────────────────────────────────────────
        content = tk.Frame(self, bg=BG)
        content.grid(row=1, column=0, sticky="nsew")
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        self._frames: list = []
        for _ in range(3):
            f = tk.Frame(content, bg=BG)
            f.grid(row=0, column=0, sticky="nsew")
            self._frames.append(f)

        self._build_step0()
        self._build_step1()
        self._build_step2()

        # ── Bottom nav ────────────────────────────────────────────────────
        bot = tk.Frame(self, bg=BG2)
        bot.grid(row=2, column=0, sticky="ew")
        bot.columnconfigure(1, weight=1)
        self._btn_back = tk.Button(
            bot, text="← " + _t("gmw.back"), bg=BG3, fg=FG,
            relief="flat", font=FONT_SM, cursor="hand2",
            padx=16, pady=8, command=self._go_back)
        self._btn_back.grid(row=0, column=0, padx=12, pady=8)
        self._btn_next = tk.Button(
            bot, text=_t("gmw.next") + " →", bg=ACCENT, fg="#ffffff",
            relief="flat", font=("Segoe UI", 10, "bold"),
            cursor="hand2", padx=16, pady=8, command=self._go_next)
        self._btn_next.grid(row=0, column=2, padx=12, pady=8)

        self._show_step(0)

    # ── Step 0 : Design picker (image cards) ──────────────────────────────
    def _build_step0(self):
        f = self._frames[0]
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)

        tk.Label(f, text=_t("gmw.pick_design"), bg=BG, fg=FG,
                 font=("Segoe UI", 14, "bold")).grid(
                     row=0, column=0, pady=(28, 14))

        # Horizontally scrollable card row
        outer = tk.Frame(f, bg=BG)
        outer.grid(row=1, column=0, sticky="nsew", padx=30, pady=(0, 10))
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        cv = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(outer, orient="horizontal", command=cv.xview)
        cv.configure(xscrollcommand=sb.set)
        sb.grid(row=1, column=0, sticky="ew")
        cv.grid(row=0, column=0, sticky="nsew")

        self._cards_inner = tk.Frame(cv, bg=BG)
        _win = cv.create_window((0, 0), window=self._cards_inner, anchor="nw")
        self._cards_inner.bind("<Configure>", lambda e: (
            cv.configure(scrollregion=cv.bbox("all")),
            cv.itemconfig(_win, height=cv.winfo_height())))
        cv.bind("<Configure>", lambda e: cv.itemconfig(_win, height=e.height))
        self._cards_cv = cv

    def _refresh_cards(self):
        for w in self._cards_inner.winfo_children():
            w.destroy()
        self._thumb_cache.clear()
        for i, tpl in enumerate(self._templates):
            self._make_card(tpl)
        # "+" add card
        add = tk.Frame(self._cards_inner, bg=BG3, cursor="hand2",
                       highlightthickness=3, highlightbackground=BG3)
        add.pack(side="left", padx=14, pady=14)
        inner = tk.Frame(add, bg=BG3, width=170, height=220)
        inner.pack_propagate(False)
        inner.pack()
        plus_lbl = tk.Label(inner, text="+", bg=BG3, fg=FG2,
                             font=("Segoe UI", 52))
        plus_lbl.place(relx=0.5, rely=0.42, anchor="center")
        hint_lbl = tk.Label(inner, text="Add template", bg=BG3, fg=FG2,
                             font=FONT_SM)
        hint_lbl.place(relx=0.5, rely=0.75, anchor="center")
        for w in (add, inner, plus_lbl, hint_lbl):
            w.bind("<Button-1>", lambda e: self._add_template())

    def _make_card(self, tpl: dict):
        from PIL import Image as _I, ImageTk as _ITk
        path = tpl.get("path", "")
        name = tpl.get("name", os.path.basename(path))
        selected = bool(self._selected_tpl and
                        self._selected_tpl.get("path") == path)

        card = tk.Frame(self._cards_inner, bg=BG3, cursor="hand2",
                        highlightthickness=3,
                        highlightbackground=ACCENT if selected else BG3)
        card.pack(side="left", padx=14, pady=14)

        # Thumbnail canvas
        tc = tk.Canvas(card, width=170, height=170, bg="#f0f0f0",
                       highlightthickness=0)
        tc.pack(pady=(12, 0), padx=12)
        try:
            img = _I.open(path).convert("RGB")
            img.thumbnail((170, 170), _I.LANCZOS)
            w_, h_ = img.size
            thumb = _ITk.PhotoImage(img)
            self._thumb_cache[path] = thumb
            tc.create_image((170 - w_) // 2, (170 - h_) // 2,
                            anchor="nw", image=thumb)
        except Exception:
            tc.create_text(85, 85, text="?", fill=FG2,
                           font=("Segoe UI", 24))

        # Name
        name_lbl = tk.Label(card, text=name, bg=BG3, fg=FG, font=FONT_SM,
                             wraplength=160, justify="center")
        name_lbl.pack(padx=10, pady=(6, 2))

        # Remove button
        del_lbl = tk.Label(card, text="✕  remove", bg=BG3, fg=FG2,
                           font=("Segoe UI", 7), cursor="hand2")
        del_lbl.pack(pady=(0, 8))

        def _select(t=tpl):
            self._selected_tpl = t
            self._refresh_cards()
            self._schedule_tile_preview()

        def _remove(t=tpl):
            if t in self._templates:
                self._templates.remove(t)
                self._save_templates()
                if self._selected_tpl.get("path") == t.get("path"):
                    self._selected_tpl = (self._templates[0]
                                          if self._templates else {})
                self._refresh_cards()

        for w in (card, tc, name_lbl):
            w.bind("<Button-1>", lambda e, fn=_select: fn())
        del_lbl.bind("<Button-1>", lambda e, fn=_remove: fn())

    def _add_template(self):
        paths = filedialog.askopenfilenames(
            parent=self,
            title="Select template image(s)",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp"),
                       ("All files", "*.*")])
        if not paths:
            return
        added = False
        for p in paths:
            p = os.path.normpath(p)
            if any(t["path"] == p for t in self._templates):
                continue
            name = os.path.splitext(os.path.basename(p))[0]
            self._templates.append({"name": name, "path": p})
            added = True
        if added:
            self._save_templates()
            if not self._selected_tpl:
                self._selected_tpl = self._templates[0]
            self._refresh_cards()
            self._schedule_tile_preview()

    # ── Step 1 : Configure ────────────────────────────────────────────────
    def _build_step1(self):
        f = self._frames[1]
        f.columnconfigure(0, weight=0)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(0, weight=1)

        # Left panel
        lp = tk.Frame(f, bg=BG2, width=310)
        lp.grid(row=0, column=0, sticky="nsew")
        lp.grid_propagate(False)

        def sec(txt):
            tk.Label(lp, text=txt, bg=BG2, fg=FG2,
                     font=("Segoe UI", 8, "bold")).pack(
                         anchor="w", padx=16, pady=(14, 3))

        def row(lbl, fn, lw=14):
            r = tk.Frame(lp, bg=BG2)
            r.pack(fill="x", padx=16, pady=4)
            if lbl:
                tk.Label(r, text=lbl, bg=BG2, fg=FG, font=FONT_SM,
                         width=lw, anchor="e").pack(side="left",
                                                    padx=(0, 7))
            fn(r)

        def spin(p, var, lo, hi, inc=1, w=7, fmt=None):
            kw = {"format": fmt} if fmt else {}
            tk.Spinbox(p, textvariable=var, from_=lo, to=hi, increment=inc,
                       width=w, bg=BG3, fg=FG, insertbackground=FG,
                       relief="flat", buttonbackground=BG3,
                       **kw).pack(side="left")

        def entry(p, var, w=18):
            tk.Entry(p, textvariable=var, bg=BG3, fg=FG,
                     insertbackground=FG, relief="flat",
                     width=w).pack(side="left", fill="x", expand=True)

        def swatch(p, var):
            ref = [None]
            def _pick():
                try:
                    ColorPickerDialog(
                        parent=self, init_color=var.get(),
                        callback=lambda c: (var.set(c),
                            ref[0].configure(bg=c) if ref[0] else None))
                except Exception:
                    pass
            b = tk.Button(p, bg=var.get(), width=2, relief="flat",
                          cursor="hand2", command=_pick)
            b.pack(side="left", padx=(4, 0))
            ref[0] = b
            var.trace_add("write", lambda *_: (
                b.configure(bg=var.get()) if b.winfo_exists() else None))

        # ── Preset ────────────────────────────────────────────────────
        sec("━  Preset")
        pr_row = tk.Frame(lp, bg=BG2)
        pr_row.pack(fill="x", padx=16, pady=4)
        self._preset_cb = ttk.Combobox(
            pr_row, textvariable=self._preset_name,
            values=[p["name"] for p in self._presets],
            state="normal", width=13, style="Custom.TCombobox")
        self._preset_cb.pack(side="left", fill="x", expand=True)
        tk.Button(pr_row, text="Load", bg=BG3, fg=FG, relief="flat",
                  font=FONT_SM, cursor="hand2",
                  command=self._load_selected_preset).pack(
                      side="left", padx=(4, 0))
        tk.Button(pr_row, text="Save", bg=ACCENT, fg="#ffffff",
                  relief="flat", font=FONT_SM, cursor="hand2",
                  command=self._save_preset_as).pack(
                      side="left", padx=(4, 0))
        tk.Button(pr_row, text="✕", bg=BG3, fg=FG2, relief="flat",
                  font=FONT_SM, cursor="hand2",
                  command=self._delete_preset).pack(
                      side="left", padx=(4, 0))

        # ── Numbers ────────────────────────────────────────────────────
        sec("━  " + _t("gmw.num_range"))
        nr = tk.Frame(lp, bg=BG2)
        nr.pack(fill="x", padx=16, pady=4)
        tk.Entry(nr, textvariable=self._num_range_str,
                 bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                 width=22, font=("Segoe UI", 11)).pack(
                     side="left", fill="x", expand=True)
        tk.Label(lp, text="ex: 1-15,  77,  100",
                 bg=BG2, fg=FG2, font=("Segoe UI", 7)).pack(
                     anchor="e", padx=18)

        # ── Number style ───────────────────────────────────────────────
        sec("━  Number")

        def _nfont(p):
            tk.Entry(p, textvariable=self._num_font_fam, bg=BG3, fg=FG,
                     insertbackground=FG, relief="flat",
                     width=13).pack(side="left", fill="x", expand=True)
            tk.Entry(p, textvariable=self._num_font_sty, bg=BG3, fg=FG,
                     insertbackground=FG, relief="flat",
                     width=7).pack(side="left", padx=(4, 0))
        row(_t("gmw.num_font"), _nfont)

        def _nauto(p):
            tk.Checkbutton(p, text=_t("gmw.num_auto"),
                           variable=self._num_auto, bg=BG2, fg=FG,
                           selectcolor=BG3, activebackground=BG2,
                           font=FONT_SM,
                           command=self._toggle_num_spin).pack(side="left")
        row("", _nauto)

        def _nfsize(p):
            self._num_fsize_spin = tk.Spinbox(
                p, textvariable=self._num_fsize_pt, from_=4, to=999,
                width=6, bg=BG3, fg=FG, insertbackground=FG,
                relief="flat", buttonbackground=BG3, state="disabled")
            self._num_fsize_spin.pack(side="left")
            tk.Label(p, text="pt  (1-2 digit)", bg=BG2, fg=FG2,
                     font=FONT_SM).pack(side="left", padx=2)
        row(_t("gmw.num_fsize"), _nfsize)

        def _nfsize3(p):
            self._num_fsize3_spin = tk.Spinbox(
                p, textvariable=self._num_fsize3_pt, from_=4, to=999,
                width=6, bg=BG3, fg=FG, insertbackground=FG,
                relief="flat", buttonbackground=BG3, state="disabled")
            self._num_fsize3_spin.pack(side="left")
            tk.Label(p, text="pt  (3+ digit)", bg=BG2, fg=FG2,
                     font=FONT_SM).pack(side="left", padx=2)
        row("  3-digit size", _nfsize3)

        def _ncol(p):
            entry(p, self._num_color, 9)
            swatch(p, self._num_color)
        row(_t("gmw.num_color"), _ncol)

        # ── Number position (slider + % label + ≈px display + direct px entry)
        def _pos_row(p, pct_var, pct_str, px_str, mm_dim_var):
            sc = tk.Scale(p, variable=pct_var, from_=0, to=100,
                          orient="horizontal", bg=BG2, fg=FG, troughcolor=BG3,
                          highlightthickness=0, showvalue=False, length=100)
            sc.pack(side="left")
            tk.Label(p, textvariable=pct_str, bg=BG2, fg=FG2,
                     font=FONT_SM, width=4, anchor="w").pack(
                         side="left", padx=(4, 0))
            tk.Label(p, textvariable=px_str, bg=BG2, fg=FG2,
                     font=("Segoe UI", 7), width=8, anchor="w").pack(
                         side="left", padx=(6, 2))
            px_ent = tk.Entry(p, bg=BG3, fg=FG, insertbackground=FG,
                              relief="flat", width=5, justify="right")
            px_ent.pack(side="left")
            tk.Label(p, text="px", bg=BG2, fg=FG2,
                     font=FONT_SM).pack(side="left", padx=(2, 0))
            def _apply(e=None, _v=pct_var, _e=px_ent, _d=mm_dim_var):
                try:
                    pv = int(_e.get())
                    dpi = max(1, self._dpi.get())
                    tile_px = max(1, round(_d.get() / 25.4 * dpi))
                    _v.set(max(0, min(100, round(pv / tile_px * 100))))
                except ValueError:
                    pass
            px_ent.bind("<Return>",   _apply)
            px_ent.bind("<FocusOut>", _apply)

        row("Position X",
            lambda p: _pos_row(p, self._num_pos_x, self._pos_x_pct_str,
                               self._pos_x_px_str, self._tile_w))
        row("Position Y",
            lambda p: _pos_row(p, self._num_pos_y, self._pos_y_pct_str,
                               self._pos_y_px_str, self._tile_h))

        # ── Output options ─────────────────────────────────────────────
        sec("━  Output")
        mn_row = tk.Frame(lp, bg=BG2)
        mn_row.pack(fill="x", padx=16, pady=4)
        tk.Checkbutton(mn_row, text=_t("gmw.monochrome"),
                       variable=self._monochrome, bg=BG2, fg=FG,
                       selectcolor=BG3, activebackground=BG2,
                       font=FONT_SM).pack(side="left")

        # ── Advanced (collapsible) ─────────────────────────────────────
        tk.Frame(lp, bg=BG3, height=1).pack(fill="x", padx=16, pady=(14, 0))
        self._adv_open = False
        self._adv_btn = tk.Button(
            lp, text="▸  " + _t("gmw.advanced"),
            bg=BG2, fg=FG2, relief="flat",
            font=("Segoe UI", 8, "bold"), cursor="hand2", anchor="w",
            command=self._toggle_adv)
        self._adv_btn.pack(fill="x", padx=14, pady=4)
        self._adv_frame = tk.Frame(lp, bg=BG2)

        def adv_row(lbl, fn, lw=14):
            r = tk.Frame(self._adv_frame, bg=BG2)
            r.pack(fill="x", padx=16, pady=3)
            if lbl:
                tk.Label(r, text=lbl, bg=BG2, fg=FG, font=FONT_SM,
                         width=lw, anchor="e").pack(side="left",
                                                    padx=(0, 7))
            fn(r)

        def _tsize(p):
            spin(p, self._tile_w, 1, 500, inc=0.5, w=7, fmt="%.1f")
            tk.Label(p, text="×", bg=BG2, fg=FG2,
                     font=FONT_SM).pack(side="left", padx=4)
            spin(p, self._tile_h, 1, 500, inc=0.5, w=7, fmt="%.1f")
            tk.Label(p, text="mm", bg=BG2, fg=FG2,
                     font=FONT_SM).pack(side="left", padx=2)
            tk.Label(p, textvariable=self._tile_px_str, bg=BG2, fg=FG2,
                     font=("Segoe UI", 7)).pack(side="left", padx=(10, 0))
        adv_row(_t("gmw.tile_size"), _tsize)
        adv_row(_t("gmw.dpi"),
                lambda p: spin(p, self._dpi, 72, 600, w=6))

        # ── Right panel : tile preview ────────────────────────────────
        rp = tk.Frame(f, bg=BG)
        rp.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        rp.rowconfigure(0, weight=1)
        rp.columnconfigure(0, weight=1)

        self._tile_canvas = tk.Canvas(rp, bg=BG3, highlightthickness=0)
        self._tile_canvas.grid(row=0, column=0, sticky="nsew")
        self._tile_canvas.bind(
            "<Configure>", lambda e: self._schedule_tile_preview(300))

        pn_row = tk.Frame(rp, bg=BG)
        pn_row.grid(row=1, column=0, pady=(8, 0), sticky="e")
        tk.Label(pn_row, text=_t("gmw.tile_preview_num"),
                 bg=BG, fg=FG2, font=FONT_SM).pack(side="left", padx=(0, 6))
        tk.Spinbox(pn_row, textvariable=self._preview_num,
                   from_=1, to=9999, width=6, bg=BG3, fg=FG,
                   insertbackground=FG, relief="flat",
                   buttonbackground=BG3).pack(side="left")

    # ── Step 2 : Print ────────────────────────────────────────────────────
    def _build_step2(self):
        f = self._frames[2]
        f.columnconfigure(0, weight=0)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(0, weight=1)

        lp = tk.Frame(f, bg=BG2, width=245)
        lp.grid(row=0, column=0, sticky="nsew")
        lp.grid_propagate(False)

        P = {"padx": 14, "pady": 4}

        def sec(txt):
            tk.Label(lp, text=txt, bg=BG2, fg=FG2,
                     font=("Segoe UI", 8, "bold")).pack(
                         anchor="w", padx=14, pady=(12, 3))

        def row(lbl, fn, lw=13):
            r = tk.Frame(lp, bg=BG2)
            r.pack(fill="x", **P)
            if lbl:
                tk.Label(r, text=lbl, bg=BG2, fg=FG, font=FONT_SM,
                         width=lw, anchor="e").pack(side="left",
                                                    padx=(0, 6))
            fn(r)

        def spin(p, var, lo, hi, inc=1, w=7, fmt=None):
            kw = {"format": fmt} if fmt else {}
            tk.Spinbox(p, textvariable=var, from_=lo, to=hi, increment=inc,
                       width=w, bg=BG3, fg=FG, insertbackground=FG,
                       relief="flat", buttonbackground=BG3,
                       **kw).pack(side="left")

        sec("━  " + _t("gmw.paper"))
        fmt_row = tk.Frame(lp, bg=BG2)
        fmt_row.pack(fill="x", padx=14, pady=(2, 6))
        for fk, fl in (
            (list(self.PAPER_MM)[0], "A3ノビ"),
            (list(self.PAPER_MM)[1], "A3"),
            (list(self.PAPER_MM)[2], "A4"),
        ):
            tk.Radiobutton(
                fmt_row, text=fl, variable=self._paper, value=fk,
                bg=BG3, fg=FG, selectcolor=ACCENT,
                font=("Segoe UI", 9, "bold"),
                indicatoron=0, padx=12, pady=5, cursor="hand2",
                relief="flat", overrelief="groove",
                activebackground=BG3, activeforeground=FG
            ).pack(side="left", padx=(0, 4))

        def _orient_w(p):
            for val, key in (("landscape", "gmw.landscape"),
                              ("portrait",  "gmw.portrait")):
                tk.Radiobutton(p, text=_t(key), variable=self._orient,
                               value=val, bg=BG2, fg=FG, selectcolor=BG3,
                               activebackground=BG2,
                               font=FONT_SM).pack(side="left", padx=3)
        row(_t("gmw.orient"), _orient_w)

        def _mm(p, var):
            spin(p, var, 0, 100, inc=0.5, w=6, fmt="%.1f")
            tk.Label(p, text="mm", bg=BG2, fg=FG2,
                     font=FONT_SM).pack(side="left", padx=2)
        row(_t("gmw.margin"), lambda p: _mm(p, self._margin))
        row(_t("gmw.gap"),    lambda p: _mm(p, self._gap))
        row(_t("gmw.dpi"),    lambda p: spin(p, self._dpi, 72, 600, w=6))

        sec("━  Cut lines")
        cut_row = tk.Frame(lp, bg=BG2)
        cut_row.pack(fill="x", padx=14, pady=4)
        tk.Checkbutton(cut_row, text="Show", variable=self._cut_line_show,
                       bg=BG2, fg=FG, selectcolor=BG3,
                       activebackground=BG2, font=FONT_SM).pack(side="left")
        tk.Spinbox(cut_row, textvariable=self._cut_line_pt,
                   from_=0.1, to=5.0, increment=0.1, width=5,
                   bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                   buttonbackground=BG3,
                   format="%.1f").pack(side="left", padx=(8, 0))
        tk.Label(cut_row, text="pt", bg=BG2, fg=FG2,
                 font=FONT_SM).pack(side="left", padx=2)

        sec("━  " + _t("gmw.out_folder"))

        def _out_w(p):
            tk.Entry(p, textvariable=self._out_folder, bg=BG3, fg=FG,
                     insertbackground=FG, relief="flat",
                     width=12).pack(side="left", fill="x", expand=True)
            tk.Button(p, text="📂", bg=BG3, fg=FG, relief="flat",
                      cursor="hand2",
                      command=self._pick_out_folder).pack(side="left",
                                                          padx=(4, 0))
        row(_t("gmw.out_folder"), _out_w)

        def _pr_w(p):
            self._printer_cb = ttk.Combobox(
                p, textvariable=self._printer, state="readonly",
                width=14, style="Custom.TCombobox")
            self._printer_cb.pack(side="left")
        row(_t("gmw.printers"), _pr_w)

        mn = tk.Frame(lp, bg=BG2)
        mn.pack(fill="x", **P)
        tk.Checkbutton(mn, text=_t("gmw.monochrome"),
                       variable=self._monochrome, bg=BG2, fg=FG,
                       selectcolor=BG3, activebackground=BG2,
                       font=FONT_SM).pack(side="left")

        sec("━  Export / Print")
        for txt, cmd in (
            (_t("gmw.export_png"), self._export_png),
            (_t("gmw.export_pdf"), self._export_pdf),
            (_t("gmw.print"),      self._print_layout),
        ):
            tk.Button(lp, text=txt, bg=ACCENT, fg="#ffffff",
                      relief="flat", font=FONT_SM, cursor="hand2",
                      padx=10, pady=7, anchor="w",
                      command=cmd).pack(fill="x", padx=14, pady=3)

        # Right panel : layout preview
        rp = tk.Frame(f, bg=BG)
        rp.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        rp.rowconfigure(0, weight=1)
        rp.columnconfigure(0, weight=1)

        self._layout_canvas = tk.Canvas(rp, bg=BG3, highlightthickness=0)
        self._layout_canvas.grid(row=0, column=0, sticky="nsew")
        self._layout_canvas.bind(
            "<Configure>", lambda e: self._schedule_layout_preview(300))

        page_nav = tk.Frame(rp, bg=BG)
        page_nav.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self._page_prev_btn = tk.Button(
            page_nav, text="◄", bg=BG3, fg=FG, relief="flat",
            cursor="hand2", state="disabled", command=self._page_prev)
        self._page_prev_btn.pack(side="left")
        tk.Label(page_nav, textvariable=self._layout_page_str,
                 bg=BG, fg=FG, font=("Segoe UI", 9, "bold")).pack(
                     side="left")
        self._page_next_btn = tk.Button(
            page_nav, text="►", bg=BG3, fg=FG, relief="flat",
            cursor="hand2", state="disabled", command=self._page_next)
        self._page_next_btn.pack(side="left")
        tk.Label(page_nav, textvariable=self._layout_info,
                 bg=BG, fg=FG2, font=FONT_SM).pack(side="right")

    # ═════════════════════════════════════════════════════════════════════
    # Navigation
    # ═════════════════════════════════════════════════════════════════════
    def _show_step(self, n: int):
        self._step = n
        for i, f in enumerate(self._frames):
            if i == n:
                f.tkraise()
        for i, (nl, tl) in enumerate(
                zip(self._step_num_lbl, self._step_txt_lbl)):
            active = (i == n)
            nl.configure(fg=ACCENT if active else FG2)
            tl.configure(fg=FG if active else FG2,
                         font=("Segoe UI", 9,
                               "bold" if active else "normal"))
        self._btn_back.configure(
            state="normal" if n > 0 else "disabled")
        if n < 2:
            self._btn_next.configure(
                text=_t("gmw.next") + " →", state="normal", bg=ACCENT)
        else:
            self._btn_next.configure(text="", state="disabled", bg=BG2)
        if n == 1:
            self._schedule_tile_preview(300)
        elif n == 2:
            self._schedule_layout_preview(300)

    def _go_next(self):
        if self._step == 0:
            if not self._selected_tpl:
                if self._templates:
                    self._selected_tpl = self._templates[0]
                    self._refresh_cards()
                else:
                    messagebox.showwarning(
                        _t("gmw.title"),
                        "Please add at least one template image first.",
                        parent=self)
                    return
        if self._step < 2:
            self._show_step(self._step + 1)

    def _go_back(self):
        if self._step > 0:
            self._show_step(self._step - 1)

    def _page_prev(self):
        idx = max(0, self._layout_page_idx.get() - 1)
        self._layout_page_idx.set(idx)
        self._show_layout_page()

    def _page_next(self):
        idx = min(len(self._layout_pages) - 1,
                  self._layout_page_idx.get() + 1)
        self._layout_page_idx.set(idx)
        self._show_layout_page()

    def _show_layout_page(self):
        if not self._layout_pages or not self.winfo_exists():
            return
        idx = max(0, min(self._layout_page_idx.get(),
                         len(self._layout_pages) - 1))
        cw = self._layout_canvas.winfo_width()
        ch = self._layout_canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        from PIL import ImageTk as _ITk
        sheet = self._layout_pages[idx]
        sw, sh = sheet.size
        scale = min(cw / sw, ch / sh, 1.0)
        nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
        self._layout_tk = _ITk.PhotoImage(sheet.resize((nw, nh)))
        self._layout_canvas.delete("all")
        self._layout_canvas.create_image(
            (cw - nw) // 2, (ch - nh) // 2,
            anchor="nw", image=self._layout_tk)
        total = len(self._layout_pages)
        self._layout_page_str.set(f"  Page  {idx + 1} / {total}  ")
        if hasattr(self, "_page_prev_btn"):
            self._page_prev_btn.configure(
                state="normal" if idx > 0 else "disabled")
        if hasattr(self, "_page_next_btn"):
            self._page_next_btn.configure(
                state="normal" if idx < total - 1 else "disabled")

    # ═════════════════════════════════════════════════════════════════════
    # Toggles
    # ═════════════════════════════════════════════════════════════════════
    def _toggle_num_spin(self):
        if hasattr(self, "_num_fsize_spin"):
            st = "disabled" if self._num_auto.get() else "normal"
            self._num_fsize_spin.configure(state=st)
        if hasattr(self, "_num_fsize3_spin"):
            st = "disabled" if self._num_auto.get() else "normal"
            self._num_fsize3_spin.configure(state=st)
        self._schedule_tile_preview()

    def _toggle_adv(self):
        self._adv_open = not self._adv_open
        if self._adv_open:
            self._adv_frame.pack(fill="x")
            self._adv_btn.configure(text="▾  " + _t("gmw.advanced"))
        else:
            self._adv_frame.pack_forget()
            self._adv_btn.configure(text="▸  " + _t("gmw.advanced"))

    # ═════════════════════════════════════════════════════════════════════
    # Pickers / printers
    # ═════════════════════════════════════════════════════════════════════
    def _pick_out_folder(self):
        d = filedialog.askdirectory(parent=self)
        if d:
            self._out_folder.set(d)

    def _load_printers_async(self):
        def _load():
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-Printer | Select-Object -ExpandProperty Name"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=(subprocess.CREATE_NO_WINDOW
                                   if hasattr(subprocess, "CREATE_NO_WINDOW")
                                   else 0))
                names = [ln.strip() for ln in result.stdout.splitlines()
                         if ln.strip()]
                self._printers = names
                if self.winfo_exists():
                    self.after(0, self._update_printer_cb)
            except Exception:
                pass
        threading.Thread(target=_load, daemon=True).start()

    def _update_printer_cb(self):
        if hasattr(self, "_printer_cb") and self._printers:
            self._printer_cb["values"] = self._printers
            if not self._printer.get():
                self._printer_cb.set(self._printers[0])

    # ═════════════════════════════════════════════════════════════════════
    # Font helpers
    # ═════════════════════════════════════════════════════════════════════
    def _font_path(self, family: str, style: str) -> str:
        _get_font_families()
        key = f"{family} {style}".strip()
        return _INSTALLED_FONTS.get(key) or _INSTALLED_FONTS.get(family, "")

    @staticmethod
    def _pt2px(pt: float, dpi: int) -> int:
        return max(1, round(pt * dpi / 72))

    def _auto_num_size(self, text: str, tw: int, th: int,
                       fp: str, dpi: int) -> int:
        """Binary-search: largest pt where `text` fits in ~70% W / 50% H."""
        from PIL import ImageDraw as _D, ImageFont as _F, Image as _I
        max_w = int(tw * 0.72)
        max_h = int(th * 0.50)
        # for 3+ digit numbers, honour the manual cap as an upper bound
        cap = (self._num_fsize3_pt.get()
               if len(text) >= 3 else 400)
        dummy = _D.Draw(_I.new("RGB", (1, 1)))

        def fits(pt: int) -> bool:
            px = max(1, round(pt * dpi / 72))
            try:
                f = _F.truetype(fp, px) if fp else _F.load_default()
            except Exception:
                f = _F.load_default()
            bb = dummy.textbbox((0, 0), text, font=f)
            return (bb[2] - bb[0]) <= max_w and (bb[3] - bb[1]) <= max_h

        lo, hi, best = 4, cap, 8
        while lo <= hi:
            mid = (lo + hi) // 2
            if fits(mid):
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    # ═════════════════════════════════════════════════════════════════════
    # Tile renderer  — template image + number overlay
    # ═════════════════════════════════════════════════════════════════════
    def _render_tile(self, n: int, tw: int, th: int, dpi: int):
        from PIL import Image as _I, ImageDraw as _D, ImageFont as _F

        path = (self._selected_tpl.get("path", "")
                if self._selected_tpl else "")
        if path and os.path.isfile(path):
            tile = _I.open(path).convert("RGB").resize((tw, th), _I.LANCZOS)
        else:
            tile = _I.new("RGB", (tw, th), "#ffffff")

        draw = _D.Draw(tile)
        fp   = self._font_path(self._num_font_fam.get(),
                               self._num_font_sty.get())
        s    = str(n)

        if self._num_auto.get():
            num_pt = self._auto_num_size(s, tw, th, fp, dpi)
        else:
            num_pt = (self._num_fsize3_pt.get()
                      if len(s) >= 3 else self._num_fsize_pt.get())

        px = self._pt2px(num_pt, dpi)
        try:
            nf = _F.truetype(fp, px) if fp else _F.load_default()
        except Exception:
            nf = _F.load_default()

        col = self._num_color.get() or "#000000"
        cx  = int(tw * self._num_pos_x.get() / 100)
        cy  = int(th * self._num_pos_y.get() / 100)
        bb  = draw.textbbox((0, 0), s, font=nf)
        nx  = cx - (bb[2] - bb[0]) // 2 - bb[0]
        ny  = cy - (bb[3] - bb[1]) // 2 - bb[1]
        draw.text((nx, ny), s, font=nf, fill=col)
        return tile

    # ═════════════════════════════════════════════════════════════════════
    # Number range parsing  — "1-15, 77, 100"
    # ═════════════════════════════════════════════════════════════════════
    def _parse_range(self) -> list:
        result: set = set()
        for part in self._num_range_str.get().split(","):
            part = part.strip()
            if "-" in part:
                try:
                    a, b = part.split("-", 1)
                    result.update(range(int(a.strip()),
                                        int(b.strip()) + 1))
                except ValueError:
                    pass
            elif part:
                try:
                    result.add(int(part))
                except ValueError:
                    pass
        return sorted(result) or [1]

    # ═════════════════════════════════════════════════════════════════════
    # Preview
    # ═════════════════════════════════════════════════════════════════════
    def _schedule_tile_preview(self, delay=450, *_):
        if self._sched_tile_id:
            self.after_cancel(self._sched_tile_id)
        self._sched_tile_id = self.after(delay, self._render_tile_preview)

    def _render_tile_preview(self):
        self._sched_tile_id = None
        if not self.winfo_exists() or self._step != 1:
            return
        cw = self._tile_canvas.winfo_width()
        ch = self._tile_canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        n  = max(1, self._preview_num.get())
        tw = max(10, round(self._tile_w.get() / 25.4 * 150))
        th = max(10, round(self._tile_h.get() / 25.4 * 150))
        tile = self._render_tile(n, tw, th, 150)
        if self._monochrome.get():
            tile = tile.convert("L").convert("RGB")
        scale = min(cw / tw, ch / th, 1.0)
        nw, nh = max(1, int(tw * scale)), max(1, int(th * scale))
        from PIL import ImageTk as _ITk
        self._tile_tk = _ITk.PhotoImage(tile.resize((nw, nh)))
        self._tile_canvas.delete("all")
        self._tile_canvas.create_image(
            (cw - nw) // 2, (ch - nh) // 2,
            anchor="nw", image=self._tile_tk)

    def _schedule_layout_preview(self, delay=700, *_):
        if self._sched_layout_id:
            self.after_cancel(self._sched_layout_id)
        self._sched_layout_id = self.after(delay, self._render_layout_preview)

    def _render_layout_preview(self):
        self._sched_layout_id = None
        if not self.winfo_exists() or self._step != 2:
            return
        cw = self._layout_canvas.winfo_width()
        ch = self._layout_canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        prev_dpi = max(36, min(72, self._dpi.get() // 4))
        pages = self._generate_layout(dpi_override=prev_dpi)
        self._layout_pages = pages
        idx = max(0, min(self._layout_page_idx.get(), len(pages) - 1))
        self._layout_page_idx.set(idx)
        self._layout_info.set(
            _t("gmw.layout_sum",
               placed=getattr(self, "_last_placed", 0),
               total=getattr(self,  "_last_total",  0),
               cols=getattr(self,   "_last_cols",   0),
               rows=getattr(self,   "_last_rows",   0)))
        self._show_layout_page()

    # ═════════════════════════════════════════════════════════════════════
    # Layout engine
    # ═════════════════════════════════════════════════════════════════════
    def _generate_layout(self, dpi_override=None) -> list:
        from PIL import Image as _I, ImageDraw as _D
        dpi = dpi_override or max(72, self._dpi.get())
        pw_mm, ph_mm = self.PAPER_MM[self._paper.get()]
        if self._orient.get() == "portrait":
            pw_mm, ph_mm = min(pw_mm, ph_mm), max(pw_mm, ph_mm)
        else:
            pw_mm, ph_mm = max(pw_mm, ph_mm), min(pw_mm, ph_mm)

        def mm2px(mm): return round(mm / 25.4 * dpi)

        PW, PH = mm2px(pw_mm), mm2px(ph_mm)
        margin = mm2px(self._margin.get())
        gap    = mm2px(self._gap.get())
        tw     = mm2px(self._tile_w.get())
        th     = mm2px(self._tile_h.get())
        if tw <= 0 or th <= 0:
            return [_I.new("RGB", (max(1, PW), max(1, PH)), "white")]

        cols     = max(1, (PW - 2*margin + gap) // (tw + gap))
        rows     = max(1, (PH - 2*margin + gap) // (th + gap))
        per_page = cols * rows
        nums     = self._parse_range()
        self._last_total  = len(nums)
        self._last_placed = len(nums)
        self._last_cols   = cols
        self._last_rows   = rows

        # Centre the tile grid on the page
        grid_w = cols * tw + (cols - 1) * gap
        grid_h = rows * th + (rows - 1) * gap
        off_x  = (PW - grid_w) // 2
        off_y  = (PH - grid_h) // 2

        cut_lw = 0
        if self._cut_line_show.get():
            cut_lw = max(1, round(self._cut_line_pt.get() * dpi / 72))

        pages = []
        for page_start in range(0, max(1, len(nums)), per_page):
            chunk = nums[page_start:page_start + per_page]
            sheet = _I.new("RGB", (PW, PH), "white")
            draw  = _D.Draw(sheet)
            for i, n in enumerate(chunk):
                r  = i // cols
                c  = i  % cols
                x0 = off_x + c * (tw + gap)
                y0 = off_y + r * (th + gap)
                sheet.paste(self._render_tile(n, tw, th, dpi), (x0, y0))
                if cut_lw:
                    draw.rectangle(
                        [x0, y0, x0 + tw - 1, y0 + th - 1],
                        outline="#888888", width=cut_lw)
            if self._monochrome.get():
                sheet = sheet.convert("L").convert("RGB")
            pages.append(sheet)

        return pages or [_I.new("RGB", (PW, PH), "white")]

    # ═════════════════════════════════════════════════════════════════════
    # Export / Print
    # ═════════════════════════════════════════════════════════════════════
    def _check_ready(self) -> bool:
        if not self._selected_tpl or not os.path.isfile(
                self._selected_tpl.get("path", "")):
            messagebox.showwarning(
                _t("gmw.title"),
                "No template selected. Go back to step 1 and pick a template.",
                parent=self)
            return False
        return True

    def _get_out_dir(self) -> str:
        base = self._out_folder.get()
        if not base:
            base = filedialog.askdirectory(parent=self,
                                           title=_t("gmw.out_folder"))
        if base:
            os.makedirs(base, exist_ok=True)
        return base

    def _export_png(self):
        if not self._check_ready():
            return
        out_dir = self._get_out_dir()
        if not out_dir:
            return
        pages = self._generate_layout()
        if not pages:
            return
        for i, sheet in enumerate(pages):
            suffix = f"_p{i + 1}" if len(pages) > 1 else ""
            out_path = os.path.join(out_dir, f"guidemark_layout{suffix}.png")
            sheet.save(out_path, "PNG")
        messagebox.showinfo(
            _t("gmw.title"),
            _t("gmw.done_png",
               path=os.path.join(out_dir, "guidemark_layout.png")),
            parent=self)
        try:
            os.startfile(out_dir)
        except Exception:
            pass

    def _export_pdf(self):
        if not self._check_ready():
            return
        out_dir = self._get_out_dir()
        if not out_dir:
            return
        pages = self._generate_layout()
        if not pages:
            return
        out_path = os.path.join(out_dir, "guidemark_layout.pdf")
        pages[0].save(
            out_path, "PDF",
            resolution=max(72, self._dpi.get()),
            save_all=True,
            append_images=pages[1:])
        messagebox.showinfo(_t("gmw.title"),
                            _t("gmw.done_pdf", path=out_path), parent=self)
        try:
            os.startfile(out_path)
        except Exception:
            pass

    def _print_layout(self):
        if not self._check_ready():
            return
        pages = self._generate_layout()
        if not pages:
            return
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        pages[0].save(tmp.name, "PNG")
        try:
            os.startfile(tmp.name, "print")
        except Exception as e:
            messagebox.showerror("Print error", str(e), parent=self)
# =============================================================================
#  HOME SCREEN
# =============================================================================
class HomeScreen(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Image Tool")
        self.configure(bg=BG)
        self.geometry("600x780")
        self.resizable(False, False)
        self._apply_style()
        self._build()

    def _apply_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Custom.TCombobox",
                        fieldbackground=BG3, background=BG3,
                        foreground=FG, selectbackground=ACCENT,
                        arrowcolor=FG)

    def _build(self):
        # ── Hero banner ───────────────────────────────────────────────────────
        hero = tk.Frame(self, bg=BG2)
        hero.pack(fill="x")
        hpad = styled_frame(hero, bg=BG2)
        hpad.pack(fill="x", padx=28, pady=(24, 20))

        row1 = styled_frame(hpad, bg=BG2)
        row1.pack(anchor="w")
        icon_box = tk.Frame(row1, bg=ACCENT, width=46, height=46)
        icon_box.pack_propagate(False)
        icon_box.pack(side="left", padx=(0, 14))
        tk.Label(icon_box, text="🖼", bg=ACCENT, fg=FG,
                 font=("Segoe UI", 20)).pack(expand=True)
        col = styled_frame(row1, bg=BG2)
        col.pack(side="left")
        label(col, "Image Tool", font=("Segoe UI", 22, "bold"), bg=BG2).pack(anchor="w")
        label(col, _t("home.sub"),
              fg=FG2, font=FONT_SM, bg=BG2).pack(anchor="w")

        pills = styled_frame(hpad, bg=BG2)
        pills.pack(anchor="w", pady=(14, 0))
        for txt in _t("home.pills"):
            p = tk.Frame(pills, bg=BG3)
            p.pack(side="left", padx=(0, 8))
            tk.Label(p, text=f"  {txt}  ", bg=BG3, fg=FG2,
                     font=("Segoe UI", 8)).pack(padx=2, pady=4)

        # Language toggle button (placed after pills, right-aligned)
        lang_row = styled_frame(hpad, bg=BG2)
        lang_row.pack(anchor="e", pady=(10, 0))
        self._lang_btn = tk.Button(
            lang_row, text=_t("home.lang_btn"),
            command=self._toggle_lang,
            bg=BG3, fg=FG, font=("Segoe UI", 9),
            relief="flat", bd=0, padx=10, pady=4, cursor="hand2")
        self._lang_btn.pack(side="right")

        # ── Section label ─────────────────────────────────────────────────────
        sec = styled_frame(self)
        sec.pack(fill="x", padx=28, pady=(20, 10))
        label(sec, _t("home.tools"), fg=FG2, font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Frame(sec, bg=BG3, height=1).pack(side="left", fill="x", expand=True, padx=(10, 0))

        # ── Tool cards ────────────────────────────────────────────────────────
        tools = [
            ("✏",  "Rename",      _t("home.rename_d"),     RenameWizard,      "#4cc9f0"),
            ("⤡",  "Resize",      _t("home.resize_d"),     ResizeWizard,      "#4895ef"),
            ("🗜", "Compress",    _t("home.comp_d"),       CompressWizard,    "#ff6b6b"),
            ("🔄", "Convert",     _t("conv.desc"),         ConvertWizard,     "#f4a261"),
            ("🎨", "Generate",    _t("home.gen_d"),        ImageGenWizard,    "#7c6af7"),
            ("📐", "Guide Mark",  _t("home.gmw_d"),        GuideMarkWizard,   "#2ec4b6"),
            ("📋", "Clipboard",   _t("home.clip_d"),       ClipboardManager,  "#f72585"),
        ]
        for ico, title, desc, cls, color in tools:
            self._tool_card(ico, title, desc, cls, color)

        # ── Pipeline ──────────────────────────────────────────────────────────
        sep2 = styled_frame(self)
        sep2.pack(fill="x", padx=28, pady=(18, 12))
        label(sep2, _t("home.adv"), fg=FG2, font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Frame(sep2, bg=BG3, height=1).pack(side="left", fill="x", expand=True, padx=(10, 0))

        pipe_frm = styled_frame(self)
        pipe_frm.pack(fill="x", padx=28)
        btn(pipe_frm, _t("home.pipeline"), lambda: PipelineWizard(self),
            primary=False).pack(side="left")
        label(pipe_frm, _t("home.pipe_desc"),
              fg=FG2, font=FONT_SM).pack(side="left")

    def _toggle_lang(self):
        global _LANG
        _LANG = "ja" if _LANG == "en" else "en"
        _update_font_constants()
        for w in self.winfo_children():
            w.destroy()
        self._build()

    def _tool_card(self, ico, title, desc, cls, accent_col):
        outer = tk.Frame(self, bg=BG2, cursor="hand2")
        outer.pack(fill="x", padx=28, pady=4)

        # Coloured left stripe
        stripe = tk.Frame(outer, bg=accent_col, width=4)
        stripe.pack(side="left", fill="y")
        stripe.pack_propagate(False)

        # Icon box
        icon_frm = tk.Frame(outer, bg=BG3, width=46, height=46)
        icon_frm.pack_propagate(False)
        icon_frm.pack(side="left", padx=14, pady=12)
        ico_lbl = tk.Label(icon_frm, text=ico, bg=BG3, fg=FG,
                           font=("Segoe UI", 17))
        ico_lbl.pack(expand=True)

        # Text
        txt_frm = tk.Frame(outer, bg=BG2)
        txt_frm.pack(side="left", fill="both", expand=True, pady=12)
        t_lbl = label(txt_frm, title, font=FONT_H2, bg=BG2)
        t_lbl.pack(anchor="w")
        d_lbl = label(txt_frm, desc, fg=FG2, font=FONT_SM, bg=BG2)
        d_lbl.pack(anchor="w")

        # Arrow
        arrow = tk.Label(outer, text="›", bg=BG2, fg=FG2,
                         font=("Segoe UI", 22), cursor="hand2")
        arrow.pack(side="right", padx=16)

        def open_tool(): cls(self)

        def on_enter(e):
            outer.config(bg=BG3); txt_frm.config(bg=BG3)
            t_lbl.config(bg=BG3); d_lbl.config(bg=BG3)
            arrow.config(bg=BG3, fg=ACCENT_LIGHT)
            stripe.config(bg=ACCENT_LIGHT)

        def on_leave(e):
            outer.config(bg=BG2); txt_frm.config(bg=BG2)
            t_lbl.config(bg=BG2); d_lbl.config(bg=BG2)
            arrow.config(bg=BG2, fg=FG2)
            stripe.config(bg=accent_col)

        for w in (outer, stripe, icon_frm, ico_lbl, txt_frm, t_lbl, d_lbl, arrow):
            w.bind("<Button-1>", lambda e: open_tool())
            w.bind("<Enter>",    on_enter)
            w.bind("<Leave>",    on_leave)

# =============================================================================
#  Entry point
# =============================================================================
if __name__ == "__main__":
    if not PILLOW_OK:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow", "-q"])
        from PIL import Image
    app = HomeScreen()
    app.mainloop()
