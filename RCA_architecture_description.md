# RCA Architecture Description

## Summary

RCA is an incremental controller-layer refactoring plan for the existing PySide6 PDF annotation editor.

It is not a full MVVM migration, and it is not strict Clean Architecture. It is closer to an MVC/MVP-style desktop application architecture, with Application Controllers / Coordinators introduced between Qt widgets and lower-level repositories or services.

The goal is to gradually decompose the current large `MainWindow` into focused controllers without rewriting the whole UI or replacing the PyMuPDF/PySide6 foundation.

## One-Sentence Description

RCA is a layered MVC/MVP-style desktop architecture for incrementally refactoring a PySide6 "God MainWindow" into thin UI widgets, application controllers, repositories/services, and domain-ish data models.

## Architectural Style

Useful labels for describing this approach to another architect:

- Desktop Application Layered Architecture
- MVC/MVP-inspired Controller Layer
- Application Controller / Coordinator pattern
- Repository pattern
- Incremental Refactoring
- Strangler-style decomposition of a God MainWindow

## Layer Overview

```text
Presentation / UI Layer
  QMainWindow, DockWidgets, Dialogs, QGraphicsView, QGraphicsScene

Application Controller Layer
  DocumentController
  AnnotationController
  NavigationController
  SearchController
  ViewHistoryController
  SettingsController

Infrastructure / Repository / Service Layer
  AnnotationRepository
  AnnotationIndex
  PdfSaveService
  PdfRenderService
  SettingsRepository

Domain-ish Model / State Layer
  DocumentSession
  AnnotationModel
  ViewLocation
  AnchorModel
  SearchQuery
  UndoAction
```

## Layer Responsibilities

### Presentation / UI Layer

The UI layer owns Qt widgets and user interaction surfaces.

Examples:

- `MainWindow`
- `PdfCanvasView`
- `AnnotationScene`
- `Annotations` dock
- `Search Annotations` dock
- `Navigation` dock
- Settings dialogs

Responsibilities:

- Create widgets, menus, toolbars, docks, and dialogs.
- Receive user input events.
- Call controllers for application actions.
- Display controller results.

It should avoid owning complex application workflow, PDF persistence rules, SQLite query construction, annotation indexing, or multi-document state transitions.

### Application Controller Layer

The controller layer organizes application use cases.

Planned controllers:

- `DocumentController`
- `AnnotationController`
- `NavigationController`
- `SearchController`
- `ViewHistoryController`
- `SettingsController`

Responsibilities:

- Coordinate application actions.
- Update application state.
- Call repositories/services.
- Tell UI widgets when to refresh.
- Keep workflows out of `MainWindow`.

The first implementation can let controllers hold a reference to `MainWindow` to reduce refactoring risk. Later, this can be tightened into a clearer application context or explicit dependency injection.

### Infrastructure / Repository / Service Layer

This layer owns concrete I/O and external resource operations.

Examples:

- PDF annotation reading/writing through PyMuPDF.
- PDF rendering.
- PDF saving, full rewrite saving, and incremental saving.
- SQLite indexing and searching.
- JSON settings loading/saving.

Repositories/services should hide low-level library details from controllers where practical.

### Domain-ish Model / State Layer

This layer contains mostly data structures.

Examples:

- `DocumentSession`
- `AnnotationModel`
- `ViewLocation`
- `AnchorModel`
- `AnnotationSearchQuery`
- `UndoAction`

The term "domain-ish" is intentional. This project is not a typical business-domain application. Much of the domain comes from PDF standards, annotations, PyMuPDF objects, view state, and search/index data.

## Relation To Common Architecture Terms

### MVC

RCA is MVC-inspired, but not strict MVC.

Qt widgets already mix some view and controller behavior, especially in event handling. RCA keeps Qt widgets as presentation objects while moving application workflows into controller classes.

### MVP

RCA is close to MVP or Supervising Controller style.

Widgets display UI and forward actions. Controllers/presenters coordinate application behavior.

### MVVM

RCA is not MVVM.

There is no QML ViewModel layer, no declarative binding system, and no attempt to model every UI state as bindable properties.

### Three-Layer Architecture

RCA can be described as a desktop variant of layered architecture:

- Presentation
- Application Controller
- Infrastructure / Repository / Service

The model/state layer supports these layers but does not imply a full enterprise-style domain layer.

### Repository Pattern

RCA uses repository-style boundaries for PDF annotations, SQLite annotation indexes, and settings persistence.

Examples:

- `AnnotationRepository`
- `AnnotationIndex`
- future `SettingsRepository`

### IoC / Dependency Injection

RCA does not require an IoC container.

Manual dependency wiring in `MainWindow` is enough for now. If the project grows, dependencies can be moved into an `AppContext` or small composition root.

### AOP

RCA does not introduce AOP.

Logging, error handling, and transaction-like save behavior can be improved later without introducing aspect-oriented infrastructure.

## Why This Fits The Current Project

The application currently has many responsibilities concentrated in `main.py`:

- Multi-document open/close/switch behavior.
- PDF saving and dirty state handling.
- Annotation selection, creation, deletion, movement, and resizing.
- Annotation property editing.
- View Back/Forward history.
- Navigation, bookmarks, anchors, and refs.
- SQLite annotation indexing and querying.
- Search result jumping.
- Settings and debug tools.

RCA allows these responsibilities to be separated gradually while keeping the working application stable after each stage.

## Planned RCA Stages

```text
RCA-1: Controller Basic Structure
  Create app/controllers/
  Establish a low-risk controller pattern.

RCA-2: ViewHistoryController
  Move Back / Forward view history logic out of main.py.

RCA-3: NavigationController
  Move bookmarks, anchors, refs, and serial-number related logic out of main.py.

RCA-4: SearchController
  Move SQLite indexing, searching, Search dock coordination, and reindex thread handling out of main.py.

RCA-5: DocumentController
  Move multi-document, open, close, save, dirty state, tabs, and recent files out of main.py.

RCA-6: AnnotationController
  Move annotation add/delete/select/move/resize/property-edit/undo logic out of main.py.

RCA-7: Directory Structure Cleanup
  Move UI widgets, repositories, models, and services into clearer packages.

RCA-8：PDF 写回层
RCA-9：状态层
RCA-9A：创建 UndoController，迁移 add/delete undo 流程
RCA-10：MainWindow 收尾 / 架构审计
  
```



## Practical Migration Principle

RCA should be implemented incrementally.

Each stage should:

- Move one coherent responsibility.
- Keep behavior unchanged.
- Preserve existing tests and manual workflows.
- Leave `MainWindow` with thin wrapper methods when needed.
- Avoid large import-path churn until the controller boundaries are stable.

The purpose is not just to make `main.py` shorter. The purpose is to make state ownership, application workflows, and persistence boundaries easier to reason about.

## Target Project Structure

The following structure is the intended long-term project layout after the RCA refactoring is complete. It is a target structure, not a statement of the current implementation state.

```text
app/
  main_window.py                         # Main UI composition root; creates the main window, menus, toolbar, docks, and controllers.

  controllers/                           # Application controller layer; owns use-case workflows and coordinates UI, models, repositories, and services.
    __init__.py                          # Controller package marker.
    document_controller.py               # Open, close, switch, save, dirty state, document tabs, and recent-file workflows.
    annotation_controller.py             # Annotation selection, creation, deletion, movement, resize, property editing, and undo workflows.
    navigation_controller.py             # Bookmarks, anchors, refs, serial markers, and navigation jump workflows.
    search_controller.py                 # SQLite indexing, search requests, reindex threading, indexed-file filtering, and search-result jumps.
    view_history_controller.py           # Back/Forward view-location history and restore behavior.
    settings_controller.py               # Application settings loading, saving, validation, and applying settings to runtime state.

  widgets/                               # Presentation widgets; owns Qt UI controls and forwards user actions to controllers.
    __init__.py                          # Widget package marker.
    pdf_canvas.py                        # QGraphicsView/QGraphicsScene canvas for page rendering and annotation overlay interaction.
    annotations_dock.py                  # Right-side Annotations dock, annotation list tab, and properties tab composition.
    navigation_dock.py                   # Left-side Navigation dock, bookmarks tab, anchors tab, and ref-related UI.
    search_dock.py                       # Search Annotations dock, query controls, file filters, and result table.
    annotation_properties.py             # Property editor widgets for FreeText, Highlight, Square, and Arrow annotations.
    annotation_list.py                   # Current-page annotation table widget.
    settings_dialog.py                   # Settings dialog UI.

  repositories/                          # Persistence and storage gateways; hides concrete storage/library details from controllers.
    __init__.py                          # Repository package marker.
    annotation_repository.py             # Read, create, update, and delete PDF annotations through PyMuPDF.
    annotation_index.py                  # SQLite annotation index schema, indexing writes, and search queries.
    settings_repository.py               # JSON settings file read/write and default-value merging.

  services/                              # Lower-level application services for PDF rendering, saving, auditing, and text extraction.
    __init__.py                          # Service package marker.
    pdf_render_service.py                # Render PDF pages to pixmaps/images with or without PDF annotation appearances.
    pdf_save_service.py                  # Full rewrite save, incremental save, backup creation, and save-error handling.
    pdf_audit_service.py                 # PDF annotation traversal audit, problematic annotation reporting, and document summaries.
    highlight_text_service.py            # Extract page text covered by highlight annotation geometry for indexing/search.

  models/                                # Data models and state objects; should avoid direct QWidget/QMainWindow dependencies.
    __init__.py                          # Model package marker.
    annotation_model.py                  # Internal annotation model shared by UI, controllers, repositories, and renderers.
    document_session.py                  # Per-open-document state: path, PyMuPDF document, page, zoom, dirty flag, and selection.
    view_history.py                      # ViewLocation model for Back/Forward history.
    anchors.py                           # Anchor/ref models, serial marker parsing, and document anchor scan data.
    search_query.py                      # Advanced annotation search query model and query-rule serialization.
    undo.py                              # Undo action model for one-level undo behavior.

  rendering/                             # Graphics item rendering and selection visuals for annotation overlays.
    __init__.py                          # Rendering package marker.
    annotation_items.py                  # Convert AnnotationModel objects into QGraphicsItem overlay items.
    annotation_selection.py              # Selection outlines, handles, highlight selected-state visuals, and hit support items.

  interaction/                           # Geometry and pointer-interaction logic independent from MainWindow.
    __init__.py                          # Interaction package marker.
    annotation_interaction.py            # Drag, move, resize, arrow endpoint movement, and interaction-result calculation.

  workers/                               # Long-running background workers used by controllers.
    __init__.py                          # Worker package marker.
    index_worker.py                      # QThread worker for full-document annotation indexing.

main.py                                  # Thin application entry point; creates QApplication and MainWindow.
RCA_architecture_description.md          # Architecture description and RCA refactoring roadmap.
PDFReaderSetting.json                    # User/application settings persisted as JSON.
PDFReaderIndex.sqlite3                   # SQLite annotation index database generated by the application.
search_rules/                            # Saved advanced-search rule files.
```

## RCA-9 State Layer Completion Notes

RCA-9 focused on separating application state from `MainWindow` while preserving the existing call style through compatibility properties. Controllers can still access fields such as `window.page_index`, `window.selected_annotation_id`, and `window.active_tool`, but those fields are now backed by `window.state`.

Completed stages:

- RCA-9A: scanned and classified `MainWindow` state fields.
- RCA-9B: created `app/app_state.py`.
- RCA-9C: moved interaction and UI synchronization state.
- RCA-9D: moved annotation view and selection state.
- RCA-9E: moved navigation-anchor state and text-line cache state.
- RCA-9F: moved undo state and search/reindex runtime state.
- RCA-9G: moved current document core state.
- RCA-9G-perf: added tab-switch and page-render timing logs.
- Navigation cache fix: cached anchor scans per open document to avoid repeated full-document scans on tab switches.
- RCA-9H: moved multi-document session state.
- RCA-9I: performed state-layer closure review.

Current `AppState` structure:

```text
AppState
  document: DocumentState
    doc: fitz.Document | None
      Current active PyMuPDF document.
    annotation_repo: object | None
      Annotation repository bound to the current active document.
    pdf_path: Path | None
      Current active PDF path.
    page_index: int
      Current active page index, 0-based.
    zoom: float
      Current canvas zoom factor.
    is_dirty: bool
      Dirty flag for the current active document.
    sessions: list[DocumentSession]
      Open PDF documents, one session per document tab.
    active_session_index: int | None
      Index of the current active document tab/session.

  annotations: AnnotationViewState
    current_annotations: list[AnnotationModel]
      Annotation models loaded for the current page.
    annotation_items: list[QGraphicsItem]
      QGraphicsItems used to render current-page annotation overlays.
    annotation_item_map: dict[str, list[QGraphicsItem]]
      Mapping from annotation model id to its overlay graphics items.
    annotation_model_map: dict[str, AnnotationModel]
      Mapping from annotation model id to annotation model.
    selection_items: list[QGraphicsItem]
      Selection outlines, handles, and selection helper graphics.
    selected_annotation_id: str | None
      Currently selected annotation model id.

  interaction: InteractionState
    active_scene_drag_kind: str | None
      Current annotation drag mode: move, resize, arrow endpoint, or none.
    active_scene_drag_annotation_id: str | None
      Annotation id involved in the current scene drag.
    active_scene_drag_start_pos: QPointF | None
      Scene position where the current drag started.
    active_tool: str | None
      Current add-annotation tool: freetext, square, highlight, arrow, or none.
    tool_start_scene_pos: QPointF | None
      Scene position where the current add-tool drag started.
    tool_preview_item: QGraphicsItem | None
      Temporary preview item for square/arrow add mode.
    tool_preview_items: list[QGraphicsItem]
      Temporary preview items for highlight add mode.

  ui_sync: UiSyncState
    updating_document_tabs: bool
      Guard flag while document tab selection is being updated programmatically.
    updating_page_spin: bool
      Guard flag while the page spin box is being updated programmatically.
    updating_table_selection: bool
      Guard flag while the annotation table selection is being synced.
    updating_scene_selection: bool
      Guard flag while QGraphicsScene selection is being synced.
    applying_property_change: bool
      Guard flag while property-panel changes are being applied to annotations.

  navigation: NavigationState
    anchor_doc_id: int | None
      Document id currently represented by the Navigation anchor view.
    anchor_dirty: bool
      Whether current document anchor data must be rescanned.
    anchor_cache: dict[int, object]
      Per-open-document cache of anchor scan results.

  search: SearchRuntimeState
    restore_geometry: object | None
      Saved geometry for restoring Search dock after maximize.
    maximized: bool
      Whether the Search dock is currently treated as maximized.
    reindex_pdf_path: Path | None
      PDF path currently being reindexed by the background worker.

  text_cache: TextCacheState
    page_index: int | None
      Page index for which `lines` was extracted.
    lines: list[list[dict]] | None
      Cached text-line extraction result used by precise highlight selection.

  undo: UndoState
    action: UndoAction | None
      Current one-level undo action.
```

State ownership after RCA-9:

`DocumentState`

- Fields: `doc`, `annotation_repo`, `pdf_path`, `page_index`, `zoom`, `is_dirty`, `sessions`, `active_session_index`.
- Meaning: the active document and the multi-document session list.
- Typical users: `DocumentController`, `CanvasController`, `NavigationController`, `SearchController`, `ViewHistoryController`, and `AnnotationController`.
- Compatibility access: `MainWindow` exposes properties such as `window.doc`, `window.page_index`, `window.zoom`, `window.sessions`, and `window.active_session_index`.

`AnnotationViewState`

- Fields: `current_annotations`, `annotation_items`, `annotation_item_map`, `annotation_model_map`, `selection_items`, `selected_annotation_id`.
- Meaning: current-page annotation view state, including loaded models, overlay graphics, and selection state.
- Typical users: `CanvasController`, `AnnotationController`, `DocumentController`, debug helpers, and annotation table/property views.
- Compatibility access: `MainWindow` exposes properties such as `window.current_annotations`, `window.annotation_model_map`, and `window.selected_annotation_id`.

`InteractionState`

- Fields: `active_scene_drag_kind`, `active_scene_drag_annotation_id`, `active_scene_drag_start_pos`, `active_tool`, `tool_start_scene_pos`, `tool_preview_item`, `tool_preview_items`.
- Meaning: transient pointer/tool interaction state for annotation dragging, resizing, endpoint movement, and add-tool previews.
- Typical users: `AnnotationController`, `PdfCanvasView`, and scene mouse-event wrappers in `MainWindow`.
- Compatibility access: `MainWindow` exposes properties such as `window.active_tool`, `window.tool_start_scene_pos`, and `window.active_scene_drag_kind`.

`UiSyncState`

- Fields: `updating_document_tabs`, `updating_page_spin`, `updating_table_selection`, `updating_scene_selection`, `applying_property_change`.
- Meaning: guard flags that prevent recursive UI signal handling while programmatically syncing widgets.
- Typical users: `DocumentController`, `AnnotationController`, page spin syncing, table syncing, scene selection syncing, and property-panel update flow.
- Compatibility access: `MainWindow` exposes properties such as `window.updating_scene_selection` and `window.applying_property_change`.

`NavigationState`

- Fields: `anchor_doc_id`, `anchor_dirty`, `anchor_cache`.
- Meaning: Navigation dock anchor refresh state and cached document-wide FreeText anchor scan results.
- Typical users: `NavigationController`, `DocumentController`, `MainWindow.mark_dirty()`, and application close handling.
- Compatibility access: `MainWindow` exposes `window.navigation_anchor_doc_id` and `window.navigation_anchor_dirty`; `anchor_cache` is accessed through `window.state.navigation.anchor_cache`.

`SearchRuntimeState`

- Fields: `restore_geometry`, `maximized`, `reindex_pdf_path`.
- Meaning: runtime state for Search dock window geometry/maximize behavior and background reindexing.
- Typical users: `SearchController` and Search dock UI actions.
- Compatibility access: `MainWindow` exposes `window.annotation_search_restore_geometry`, `window.annotation_search_maximized`, and `window.reindex_pdf_path`.

`TextCacheState`

- Fields: `page_index`, `lines`.
- Meaning: cached text-line extraction result for precise text selection/highlight creation on the current page.
- Typical users: `CanvasController.highlight_rects_from_text_flow()` and related highlight selection helpers.
- Compatibility access: `MainWindow` exposes `window.text_lines_cache_page_index` and `window.text_lines_cache`.

`UndoState`

- Fields: `action`.
- Meaning: one-level undo state for annotation add/delete/move/resize/property operations.
- Typical users: `AnnotationController`, `DocumentController`, and `MainWindow` undo wrappers.
- Compatibility access: `MainWindow` exposes `window.undo_action`.

Important performance note:

The tab-switch slowdown observed after RCA-9G was not caused by property bridging. Timing logs showed page pixmap rendering, annotation reading, and overlay rendering were fast. The slow path was the UI refresh stage, specifically repeated full-document anchor scans in `refresh_navigation()`. The fix was to cache `scan_document_anchor_data()` results per open document and clear the cache when a document session closes or the application exits.

RCA-9 conclusion:

The state layer is now structurally complete. Remaining `MainWindow` fields are mostly UI object references, controller/service references, runtime settings, debug/index objects, and worker/thread handles. Those are acceptable to leave outside `AppState` for now.

Recommended next RCA stage:

RCA-10 should focus on `MainWindow` slimming and UI composition extraction: action/menu creation, toolbar setup, dock construction, settings dialog wiring, debug UI, and remaining thin wrapper organization.
