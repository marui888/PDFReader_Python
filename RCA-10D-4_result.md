# RCA-10D-4 Result

## Purpose

RCA-10D-4 is a documentation checkpoint after the RCA-10D refactor.

It records:

- What RCA-10D changed.
- What `main.py` still owns.
- Why package directories such as `services/`, `repositories/`, and `widgets/` have not been introduced yet.
- A suggested next packaging stage.

No runtime behavior is changed by this step.

## RCA-10D Summary

RCA-10D focused on reducing View/UI helper responsibilities in `main.py`.

### RCA-10D-1

Created:

```text
app/main_window_view.py
```

Moved these View/UI helper responsibilities out of `main.py`:

- Scroll-boundary status label creation.
- `sync_page_spin`.
- `update_window_title`.
- `show_scroll_boundary_status`.
- `clear_scroll_boundary_status`.

`main.py` now keeps thin compatibility wrappers for those calls.

### RCA-10D-2

Cleaned annotation overlay helper leftovers in `main.py`.

Removed unused or obsolete overlay helpers from `main.py`:

- `add_arrow_head_lines`.
- `add_annotation_item`.
- `add_hit_item`.
- `scene_point`.
- `pdf_color`.
- `highlight_polygons`.
- `arrow_head_points`.
- `arrow_head_flags`.

Kept only compatibility wrappers still used by controllers:

- `render_annotation_overlay`.
- `refresh_annotation_overlay`.
- `clear_annotation_items`.
- `is_draggable_model`.
- `scene_rect`.
- `arrow_points`.

Actual overlay behavior remains in:

```text
app/controllers/canvas_controller.py
app/annotation_items.py
app/annotation_selection.py
```

### RCA-10D-3

Removed old thin wrappers from `main.py` that no longer had external call sites.

Examples removed:

- Document tab internal helpers.
- Recent-file lookup helpers.
- Unsaved-confirmation internal helpers.
- Search formatting/report helper wrappers.
- Unused view-history wrappers.

Kept wrappers that still function as Qt signal slots, menu/action callbacks, worker callbacks, or controller compatibility entry points.

## Current `main.py` Responsibilities

After RCA-10D, `main.py` is still not a pure shell, but it is closer to a composition root plus compatibility facade.

It currently still owns:

- Application startup and `MainWindow` construction.
- Controller creation.
- State object creation and property bridges into `AppState`.
- Central canvas and tab container setup.
- Compatibility methods used by Qt signals and controller callbacks.
- Some debug/audit helper methods.
- Some property-editor callback forwarding.
- Some annotation undo restore helpers.
- Application-level event handlers such as `keyPressEvent` and `closeEvent`.

This is acceptable for the current refactor stage because many controllers still call `window.xxx()` as a stable compatibility API.

## Why No `services/`, `repositories/`, `widgets/` Directories Yet

The project already has those roles, but they are still expressed mostly as modules instead of package directories.

Current repository-like module:

```text
app/annotation_repository.py
```

Current widget-like modules:

```text
app/annotation_list.py
app/annotation_properties.py
app/annotation_search.py
app/navigation.py
```

Current service-like modules:

```text
app/annotation_index.py
app/pdf_audit.py
app/pdf_annotation_writer.py
app/settings.py
app/index_worker.py
```

The directories were not introduced earlier because the project is still in an incremental refactor phase.

Moving files too early would create a large import-only diff, make bug tracking harder, and risk distracting from the more important task: stabilizing responsibilities and behavior first.

## Suggested Future Stage

A good future stage is:

```text
RCA-11: Package Layout Refactor
```

Suggested target layout:

```text
app/
  controllers/
    annotation_controller.py
    canvas_controller.py
    document_controller.py
    navigation_controller.py
    search_controller.py
    view_history_controller.py

  widgets/
    annotation_list.py
    annotation_properties.py
    annotation_search.py
    navigation.py

  repositories/
    annotation_repository.py

  services/
    annotation_index.py
    index_worker.py
    pdf_annotation_writer.py
    pdf_audit.py
    settings.py

  views/
    main_window_actions.py
    main_window_dialogs.py
    main_window_docks.py
    main_window_view.py
    pdf_canvas.py

  models/
    models.py
    document_session.py
    app_state.py
    undo.py
    view_history.py

  anchors.py
  annotation_interaction.py
  annotation_selection.py
  annotation_items.py
```

This should be done only after the current controller and state boundaries feel stable.

## Recommended Next Step

Before RCA-11, one more useful cleanup stage could be:

```text
RCA-10E: MainWindow Debug/Audit Helper Extraction
```

Possible scope:

- Move debug log formatting helpers.
- Move current-page state debug report building.
- Move audit report display wrappers.
- Keep `main.py` as the application shell and compatibility facade.

This is lower risk than a package-layout move and would continue reducing `main.py` before directory restructuring.
