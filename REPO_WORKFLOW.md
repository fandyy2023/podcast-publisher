# Repository Workflow Cheat-Sheet

This project (and its sibling *madeira_rentals_search*) live in your **fandyy2023** GitHub account.

## 1. Repository Mapping

| Project | Local Path | Remote URL | Default Branch |
|---------|------------|------------|----------------|
| podcast-publisher | `/Users/poolpooly/CascadeProjects/podcast-publisher` | `https://github.com/fandyy2023/podcast-publisher.git` | `main` |
| madeira_rentals_search | `/Users/poolpooly/CascadeProjects/madeira_rentals_search` | `https://github.com/fandyy2023/rentals_search.git` | `main` |

Notes for **podcast-publisher**:
* Large media (audio, images) are **ignored** via `.gitignore`. They stay only on disk or should be stored via Git LFS/cloud.

## 2. Typical Push Workflow

```bash
# 1. Check status
git status

# 2. Stage changes (all or selective)
git add -A            # or: git add <files>

# 3. Commit
git commit -m "Meaningful message"

# 4. Push to default branch
git push origin main
```

### Feature Branches
```bash
# Create and switch
git checkout -b feature_name
# ... commit as usual ...

git push -u origin feature_name   # first push
```

After the first push you can open a Pull Request on GitHub if review is needed.

## 3. Description & Summary Formatting Pipeline

> This section documents the text-formatting logic we spent the most time on: converting **plain-text ⇄ HTML**, live preview, overlays, and the character counters.  Keep it up-to-date if the implementation changes.

### 3.1  Why the Pipeline Exists
Musical platforms (Apple Podcasts, Spotify, etc.) expect HTML with very limited tags (`<p>`, `<ul>`, `<ol>`, `<li>`, `<a>`).  Editors, however, are friendlier when authors type *plain text*.  The pipeline converts back and forth so:
1.  **Plain text → safe HTML** before saving (preserves paragraphs & lists).
2.  **HTML → plain text** when opening a form / inline editor (so the textarea shows clean text).
3.  HTML is **sanitized** before it goes to RSS.

### 3.2  Where the Logic Lives
Back-end (Python)
* `utils.plain_text_to_html(text)` – converts text to HTML (`<p>`, lists). Used when saving description/summary.
* `utils.sanitize_html_for_rss(html)` – strips unwanted tags/attrs.
* `utils.html_to_plain_text(html)` – reverse conversion for editors.

Front-end (JavaScript)
* `static/js/plaintext2html.js` – same logic as `plain_text_to_html` but runs in browser for **live preview**.
* `static/js/html2plaintext.js` – reverse conversion in browser (inline-edit after save).
* `static/js/preview_sanitize.js` – strips disallowed tags for previews.
* `static/js/preview_collapsible.js` – collapsible preview blocks.
* `static/js/episode_duration.js` (unrelated counters) – counts duration; character counters are inline in each template.

### 3.3  Flow per Screen
| Screen | Edit Action | Conversion & Save Path |
|--------|-------------|------------------------|
| **`edit_show.html` / `edit_episode.html`** | Typed in `textarea` | ① Keyup → JS converts PT→HTML for preview.<br>② Submit form → Flask: `plain_text_to_html` → `sanitize_html_for_rss` → DB/RSS. |
| **Inline edit (click text) on `show.html` / episode card** | Click text → textarea appears with **plain text** (via `html_to_plain_text`). | On save (PATCH) → server converts & saves as above. |
| **Inline edit on main show list (`show_list.html`)** | Same as above; after PATCH response JS runs `htmlToPlainText()` to update the visible span without tags. |

### 3.4  Overlay & Expand/Collapse
* Elements with class `.desc-container` wrap each description.
* `.desc-edit-overlay` (semi-transparent layer) shows "Edit" on hover.
* Clicking description: first click **expands**, second click enters **inline edit** (`makeEditable` helper).  Outside click collapses.

### 3.5  Character Counters
* Each `<textarea>` with `data-maxlength` has a sibling `<span class="char-count">`.
* JS (in template or `preview_collapsible.js`) listens `input` → updates counter `(current / max)` and turns red when limit exceeded.
* Fields with counters: `title` 100 chars, `subtitle` 150, `summary` 4000.

### 3.6  Gotchas & Tips
* If HTML tags appear in list views again, ensure `|striptags` filter or `htmlToPlainText()` is applied when rendering/patching.
* Backend & frontend conversion **must stay in sync**.  Update both sides if rules change.
* Large descriptions: expand/collapse triggers after `-webkit-line-clamp` detects overflow.

---

## 4. Updating Local Code
```bash
git pull origin main   # fetch latest changes before starting work
```

## 4. Troubleshooting
* If `git push` is rejected due to large files (>100 MB), either add them to `.gitignore` or use Git LFS.
* Ensure you’re inside the correct project directory before running git commands.


## 5. Audio Transcoding Logic

The server guarantees published audio meets podcast-platform recommendations (≥160 kbps stereo MP3):

* **Constant** `MIN_PODCAST_BITRATE = 160` kbps – the absolute floor.
* **select_mp3_bitrate(src_kbps)**
  * Validates `src_kbps`.
  * `effective_src = max(src_kbps, MIN_PODCAST_BITRATE)` so we never choose below the floor.
  * Picks the first value from `[32,40,48,56,64,80,96,112,128,160,192,224,256,320]` that is ≥ `effective_src`.
  * Caps at **320 k**.
* **check_transcoding_needed(path)**
  1. If the file is not MP3 → convert.
  2. If MP3 *and* bitrate < `MIN_PODCAST_BITRATE` → convert.
  3. Otherwise keep the original.
* **process_audio_background()**
  * Runs in a thread after upload.
  * Calls `check_transcoding_needed`.
  * If conversion required → uses `select_mp3_bitrate` → `transcode_audio_to_mp3` (ffmpeg, stereo, 44.1 kHz).
  * Updates `metadata.json` accordingly and deletes the original file.

---
_Last update: 2025-07-11_
