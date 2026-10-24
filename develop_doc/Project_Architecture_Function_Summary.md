# PDF Note Reader Project Architecture And Function Summary

Generated: 2026-07-02

## 1. Project Positioning

This project is a Python desktop PDF reader and PDF standard annotation editor.

Its main goal is not only to display PDF pages, but also to create, edit, search, index, and save PDF annotations in a way that remains compatible with mainstream PDF readers such as Foxit Reader and Adobe Acrobat.

The current technical stack is:

- PySide6: desktop UI framework.
- PyMuPDF: PDF rendering and PDF standard annotation read/write.
- QGraphicsView / QGraphicsScene: page display, annotation overlay rendering, selection, drag, resize, and inline editing.
- SQLite: annotation indexing and search.
- QPDF external tool integration: PDF check/rewrite diagnostics around save safety.
- PyInstaller: Windows executable packaging.

The application has moved from an early single-file prototype toward an RCA-style layered desktop architecture.

## 2. High-Level Architecture

The project currently follows a layered MVC/MVP-inspired desktop architecture:

```text
main.py / MainWindow
  UI composition root and compatibility facade.

app/main_window/
  Menus, actions, docks, settings dialog, and view setup helpers.

app/controllers/
  Application workflow controllers.

app/canvas/
  QGraphicsView / QGraphicsScene interaction and annotation overlay rendering.

app/models/
  Application state and data models.

app/repositories/
  PDF annotation reading gateway.

app/services/
  PDF writing, indexing, auditing, settings, external tools, and text extraction.

app/widgets/
  Reusable UI widgets for docks, search, properties, navigation, shortcuts, and batch tools.
```

The most important architectural rule is:

PDF annotations are the persistence layer. QGraphicsItems are the interactive view layer.

The application reads PDF annotations into internal models, renders those models as graphics overlay items, lets the user interact with the overlay, and writes final changes back as PDF standard annotations.

## 3. Runtime Data Flow

For a normal annotated page, the flow is:

1. PyMuPDF opens the PDF document.
2. A page is rendered to a pixmap, usually without PDF annotation appearances for editable overlay workflows.
3. AnnotationRepository reads PDF annotations from the page.
4. PDF annotation objects are converted to AnnotationModel instances.
5. AnnotationItemRenderer converts AnnotationModel objects into QGraphicsItems.
6. AnnotationSelectionRenderer draws selection borders and handles.
7. User interaction modifies models or writes changes through PdfAnnotationWriter.
8. The page overlay is refreshed.
9. Save or Save Incremental persists changes into the PDF file.

This separates:

- PDF file content and annotation dictionaries.
- Internal application annotation models.
- QGraphicsScene visual/interactive overlay.
- UI panels such as annotation list and properties.

## 4. Important Directories And Files

### main.py

`main.py` is still the application entry point and main UI composition object.

Important roles:

- Creates QApplication/MainWindow.
- Owns top-level UI references.
- Wires controllers, docks, canvas, settings, and actions.
- Provides compatibility wrapper methods while RCA refactoring continues.
- Bridges older call sites to newer controllers and state objects.

Although `main.py` is still large, many responsibilities have been moved into controllers, services, widgets, and models.

### app/controllers/

The controller layer owns application workflows.

Important files:

- `document_controller.py`: document open, close, save, tab switch, recent files, dirty state, and multi-document workflows.
- `annotation_controller.py`: annotation selection, add, delete, move, resize, property changes, undo, context menus, inline editing, text selection, and continuous-view annotation interaction.
- `canvas_controller.py`: page rendering, continuous page layout, visible page rendering, overlay refresh, and scene/pixmap coordination.
- `navigation_controller.py`: bookmarks, anchors, refs, serial numbers, navigation dock actions, and reference jumps.
- `search_controller.py`: annotation indexing, search dock coordination, indexed document filters, search results, and background reindex completion.
- `view_history_controller.py`: Back/Forward view history and navigation location restore.

These controllers currently still hold a reference to MainWindow. This is intentional for incremental refactoring and avoids a risky full rewrite.

### app/canvas/

This package owns graphics-scene behavior.

Important files:

- `pdf_canvas.py`: custom QGraphicsView and QGraphicsScene event handling, including mouse, wheel, pan, boundary page turning, and canvas event forwarding.
- `annotation_items.py`: converts AnnotationModel into QGraphicsItems for Highlight, FreeText, Square, and Arrow.
- `annotation_selection.py`: draws selected-state visuals, resize handles, arrow endpoint handles, and highlight selection visuals.
- `annotation_interaction.py`: small interaction helper for detecting move/resize/endpoint drag intent.
- `inline_freetext_editor.py`: inline FreeText editor implementations.

`inline_freetext_editor.py` currently contains two implementations:

- Old path: `QPlainTextEdit + QGraphicsProxyWidget`.
- New path: `QGraphicsTextItem`.

The new QGraphicsTextItem path is the recommended default because it is native to QGraphicsScene and avoids QWidget/proxy viewport height problems.

The old path is kept as a fallback and can still be selected through Settings.

### app/models/

This package contains data structures and state containers.

Important files:

- `annotation_model.py`: internal representation of PDF annotations.
- `app_state.py`: structured application state split into document, annotation, interaction, UI sync, navigation, search, text cache, and undo state.
- `document_session.py`: per-open-document session state for multi-document tabs.
- `continuous_view.py`: continuous page layout/page item state.
- `undo.py`: one-level undo action model.
- `view_history.py`: Back/Forward view location model.

The important design choice is that controllers and UI code use these application models instead of directly manipulating PyMuPDF objects everywhere.

### app/repositories/

This layer reads PDF data into application models.

Important file:

- `annotation_repository.py`: traverses PyMuPDF annotations and converts them into AnnotationModel instances.

It supports recognized annotation types:

- FreeText
- Highlight
- Square
- Line/Arrow
- Unsupported annotations are preserved as unsupported models for diagnostics/listing.

### app/services/

This package owns lower-level application services and external integrations.

Important files:

- `pdf_annotation_writer.py`: creates, updates, moves, resizes, deletes, and normalizes PDF annotations through PyMuPDF.
- `annotation_index.py`: SQLite schema, indexing writes, search queries, indexed document information, and clear-index operations.
- `index_worker.py`: background worker for long-running document indexing.
- `pdf_text_extract.py`: text extraction by geometry, especially for highlight-covered text.
- `pdf_audit.py`: current-page/document annotation audit and bounds checks.
- `pdf_external_tools.py`: backup creation and QPDF check/rewrite command integration.
- `settings.py`: JSON settings load/save and default merging.
- `shortcuts.py`: default shortcuts and shortcut registration.
- `freetext_batch.py`: batch FreeText find/replace/delete/add transformations.
- `keyword_repository.py`: metadata keyword file loading for inline FreeText shortcut insertion.

### app/widgets/

This package contains reusable UI widgets.

Important files:

- `annotation_list.py`: current page annotation table/list.
- `annotation_properties.py`: property editor for FreeText, Highlight, Square, and Arrow.
- `annotation_search.py`: search panel, indexed document filter, result table, pagination/page-size behavior.
- `freetext_batch.py`: Batch FreeText tab UI.
- `keyword_popup.py`: keyword popup menu for inline FreeText editing.
- `navigation.py`: navigation dock widgets for bookmarks/anchors/refs.
- `shortcut_editor.py`: shortcut input widget used by Settings.

### app/main_window/

This package contains MainWindow composition helpers.

Important files:

- `actions.py`: QAction creation and menu/toolbar actions.
- `dialogs.py`: settings dialog and common dialogs.
- `docks.py`: dock widget construction and refresh helpers.
- `view.py`: central PDF view and view-related setup.

This package reduces UI construction code inside `main.py`.

## 5. Core Feature Summary

### PDF Document Management

Supported functions:

- Open PDF.
- Close current PDF.
- Close document tab.
- Multi-document tabs.
- Recent files.
- Restore last page for recent file.
- Save.
- Save As.
- Save Incremental.
- Dirty state tracking.
- Unsaved-changes prompt.
- Optional backup and QPDF check during Save Incremental.
- Reopen document after Save Incremental to keep PyMuPDF document state fresh.

Important design note:

Because PDF incremental saving and annotation updates can create compatibility issues with external readers, Save Incremental is treated carefully and can trigger backup/QPDF validation.

### Page Rendering And View Modes

Supported view modes:

- Single page view.
- Continuous page view.

Single page view:

- One PDF page is rendered at a time.
- Annotation editing is mature.
- Page navigation and scroll-position rules are tuned for reading behavior.

Continuous view:

- Multiple pages are laid out vertically.
- Visible pages are rendered and cached.
- Annotation overlay items are created per visible page.
- Editing support has been added for major annotation workflows.

Important continuous-view capabilities:

- Page center/visible-page current-page tracking.
- Annotation list and selection coordination.
- Continuous scroll rendering/unloading.
- FreeText, Square, Arrow, Highlight editing in continuous view.
- Continuous-view text selection and highlight creation.

### Zoom, Scroll, And Navigation

Supported functions:

- Zoom in/out.
- Ctrl + mouse wheel zoom.
- Mouse middle-button pan.
- PageUp/PageDown and wheel boundary page turning.
- Top/bottom boundary hints in status bar.
- Back/Forward view history.
- Toolbar Back/Forward buttons.
- Alt+Left / Alt+Right shortcuts.
- Page number jump.
- Search result jump.
- Anchor/ref jump.

### Annotation Types

The main supported annotation types are:

- FreeText: typewriter-style text annotation.
- Highlight: text highlight annotation.
- Square: rectangle annotation.
- Arrow: line annotation with arrow ending.

Unsupported annotations are detected and displayed in the annotation list/debug/audit context, but they are not rendered as editable overlay items.

### Annotation Editing

Supported operations:

- Select annotation.
- Move annotation.
- Resize FreeText and Square.
- Move Arrow.
- Edit Arrow endpoints.
- Change Highlight color and opacity.
- Change Square/Arrow color and line width.
- Change FreeText content, font size, and color.
- Delete annotation.
- Add annotation.
- One-level undo for key operations.
- Right-click context menus.
- Property panel editing.

Important interaction policy:

The application tries to keep selection and editing behavior consistent across Text mode and annotation modes. If the pointer is over an editable existing annotation, editing/selection takes priority over adding a new annotation.

### FreeText Annotation Editing

FreeText is one of the most important annotation types in this application.

Supported FreeText functions:

- Add FreeText.
- Inline edit FreeText.
- Popup input mode as a setting.
- Property-panel text editing.
- Font size editing.
- Color editing.
- Batch FreeText operations.
- Anchor/ref parsing.
- Serial-number insertion/removal.
- Keyword shortcut insertion from metadata files.

Current inline editor design:

- Preferred path: QGraphicsTextItem editor.
- Fallback path: QPlainTextEdit inside QGraphicsProxyWidget.
- A setting controls which inline editor is used.

Reason for QGraphicsTextItem:

QGraphicsTextItem is a native QGraphicsScene item and avoids viewport/scrolling/sizing problems caused by embedding QWidget editors inside a QGraphicsScene.

### Highlight Creation And Text Selection

Supported functions:

- Text mode selection with grey selection rectangles.
- Highlight mode selection that creates PDF Highlight annotations.
- Double-click word selection.
- Double-click word highlight in Highlight mode.
- Character-level text selection using PyMuPDF text extraction.
- Highlight color toolbar.
- Highlight opacity setting.
- Default highlight color management.
- Copy selected text or selected Highlight-covered text with Ctrl+C.

Important design note:

Highlight creation uses PyMuPDF text extraction and generated geometry. Commercial PDF readers may use their own text layout and selection heuristics, so exact visual parity is difficult, but the app uses character-level extraction to get close.

### Annotation List And Properties Dock

The right-side dock is named Annotations.

It contains tabs such as:

- Annotation List.
- Properties.
- Batch FreeText.

Annotation List:

- Shows current page annotations.
- Supports row selection to select page overlay annotation.
- Marks unsupported annotations.
- Can display quick audit result text.

Properties:

- Shows editable properties for the selected annotation.
- Updates overlay/PDF annotation in real time.
- Supports FreeText, Highlight, Square, and Arrow properties.

### Navigation Dock

The left-side dock supports PDF navigation data.

Important functions:

- PDF outline/bookmarks.
- Anchors.
- References.
- FreeText serial markers.
- Jumping to anchors/refs.

Anchor/ref design:

- FreeText annotations can contain serial markers such as circled-number markers.
- FreeText content can contain reference markers such as page-marker references.
- The app scans FreeText annotations to build an application-level anchor/ref map.

This is primarily application-level navigation metadata stored in FreeText contents, not necessarily PDF Named Destinations.

### SQLite Annotation Index And Search

The app supports SQLite-backed annotation indexing.

Indexed information includes:

- PDF document path.
- Indexed time.
- File modification time.
- Page index.
- Annotation xref.
- Annotation type.
- Text/content.
- Subject/source text markers for Highlight text.
- Color/opacity.
- Geometry.

Search functions:

- Current indexed PDF search.
- Indexed file filter.
- All files option.
- Type filter.
- Keyword search.
- Advanced text search rules.
- Page-size setting.
- Clear index.
- Indexed database information summary.

For Highlight annotations, the app can optionally extract the PDF text covered by the highlight geometry during indexing. This is slower but improves search usefulness.

### Batch FreeText

Batch FreeText supports file-level/page-level operations over FreeText annotations.

Main workflow:

1. Find matching FreeText annotations.
2. Select results.
3. Replace matching text.
4. Delete matching text.
5. Add text relative to matches.
6. Delete FreeText annotations.

Batch editing uses safer FreeText update paths to reduce Foxit/Acrobat appearance compatibility issues.

### Audit And External Tools

Audit functions:

- Quick Audit current page.
- Optional detailed bounds check.
- Continuous view state audit.
- Document-level audit summary.
- Bad annotation traversal warnings.

External tool integration:

- Backup current PDF.
- QPDF check.
- QPDF rewrite/check report output.
- Settings option for QPDF bin directory.

QPDF output is written as report files beside the PDF.

### Settings And Shortcuts

Settings are persisted in:

`PDFReaderSetting.json`

Important settings:

- Experimental Foxit Typewriter compatibility.
- Popup input for FreeText.
- Graphics inline FreeText editor.
- Default FreeText font size.
- FreeText font-size min/max.
- Highlight extraction during reindex.
- Quick Audit detailed bounds check.
- Save Incremental backup/QPDF default.
- Search page size.
- QPDF bin directory.
- Keyboard shortcuts.

Shortcut settings:

- Shortcut tab in Settings.
- Default shortcuts.
- Editable shortcuts.
- Conflict detection and red highlighting.
- Apply / Apply and Close / Cancel behavior.

## 6. Important Design Decisions

### PDF Standard Annotations Are The Persistence Format

The app does not invent a separate annotation file format for normal edits.

When the user saves, annotation changes are written back into the PDF as standard annotations where possible. This is why annotations can be opened and rendered by Foxit, Acrobat, and other PDF readers.

### QGraphicsScene Is The Interaction Layer

PyMuPDF can render PDF pages and annotations, but it does not provide desktop interactive editing UI.

Therefore the application renders annotations as QGraphicsItems and lets the user interact with those items.

The overlay is not the persistent source of truth. It is the editable view representation.

### AnnotationModel Is The Bridge

AnnotationModel is the bridge between:

- PDF annotation dictionaries.
- QGraphics overlay items.
- Property panel data.
- Search/index data.
- Undo records.

This model keeps the rest of the application from depending too heavily on raw PyMuPDF annotation objects.

### Continuous View Uses Page Layout Records

Continuous view does not simply render one huge PDF image.

It maintains per-page layout records and renders visible pages. This makes it possible to:

- Avoid rendering thousands of pages at once.
- Map scene coordinates back to PDF page coordinates.
- Edit annotations on visible pages.
- Refresh only affected page overlays where possible.

### Save Incremental Is Treated Carefully

The app has encountered cases where PDFs saved after annotation changes can be accepted by PyMuPDF but rejected by Foxit/QPDF.

Current mitigations include:

- Optional backup before Save Incremental.
- Optional QPDF check after Save Incremental.
- Reopen PDF after incremental save.
- Full Save path kept separate.
- Debug reports and qpdf report files.

This remains an important risk area.

## 7. Current Important Risks And Notes

### PDF Save Compatibility

PyMuPDF is the core PDF writer, but external readers may be stricter or interpret appearance streams differently.

Known sensitive areas:

- FreeText appearance streams.
- Incremental save after repeated annotation edits.
- Existing commercial-reader-generated FreeText annotations.
- Mixed Chinese/English FreeText appearance.
- Annotation xref and repaired PDFs.

Operational recommendation:

Use Save Incremental with backup/QPDF check enabled for important PDFs.

### FreeText Appearance Compatibility

FreeText is not visually identical across all PDF readers.

Differences can include:

- Font fallback.
- Font size interpretation.
- Baseline position.
- Text wrapping.
- Border/appearance stream handling.
- Foxit Typewriter-specific metadata.

The app provides an experimental Foxit compatibility option, but perfect cross-reader parity is not guaranteed.

### Continuous View Editing Complexity

Continuous view now supports editing, but it is more complex than single-page view.

Sensitive parts:

- Scene-to-page coordinate mapping.
- Visible page render/unload.
- Annotation list synchronization.
- Selection state across page refresh.
- Moving annotations near page boundaries.
- Text selection and highlight creation.

### Inline FreeText Editor Dual Path

The current recommended inline editor is QGraphicsTextItem.

The old QPlainTextEdit/QGraphicsProxyWidget path remains for fallback, but it had problems around:

- Widget viewport height.
- Hidden internal scrolling.
- Proxy widget size synchronization.
- Multi-line Chinese text display while editing.

The new graphics editor path better matches the QGraphicsScene overlay architecture.

### MainWindow Is Still A Compatibility Facade

The project has been significantly refactored, but MainWindow is still important.

It still:

- Owns top-level widgets.
- Wires controllers.
- Provides wrapper methods for compatibility.
- Holds runtime settings.

Future refactoring can continue slimming MainWindow, but the current structure is already much clearer than the original single-file approach.

## 8. Suggested Future Work

Reasonable next steps:

1. Continue MainWindow slimming by moving remaining UI composition and wrapper-heavy flows into controllers or main_window helpers.
2. Extract PDF save logic into a dedicated save service if save compatibility work grows.
3. Add automated smoke tests for annotation add/edit/save/reopen flows.
4. Add small regression scripts for FreeText appearance and QPDF check workflows.
5. Improve advanced search UI and rule persistence.
6. Strengthen continuous view performance and edit-state tests.
7. Consider a document/session-level event bus or app context if controller dependencies grow too tangled.

## 9. Mental Model For Future Development

When adding a new feature, decide which layer owns it:

- UI widget only: put it in `app/widgets` or `app/main_window`.
- User workflow: put it in a controller.
- PDF read/write: put it in repository/service.
- Graphics overlay: put it in `app/canvas`.
- Persistent setting: put it in `app/services/settings.py` and Settings UI.
- Search/index behavior: put it in `app/services/annotation_index.py`, `app/search`, or SearchController.
- Application state: put it in `app/models/app_state.py` if it is shared runtime state.

The safest development style for this project is incremental:

1. Read PDF state into models.
2. Render models into overlay items.
3. Interact with overlay items.
4. Write changes back through PdfAnnotationWriter.
5. Refresh only the affected page/view.
6. Verify with an external PDF reader when persistence compatibility matters.
