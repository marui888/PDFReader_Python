# Continuous Page View Plan

## Goal

Implement a commercial-reader-like continuous scrolling mode, where previous and next PDF pages can be viewed in one vertically scrollable canvas.

The current app renders one page at a time:

```text
current page_index
-> render one PyMuPDF page pixmap
-> place one page item in QGraphicsScene
-> render current page annotation overlay
```

Continuous page view should become:

```text
one QGraphicsScene contains many page slots
visible pages are rendered as pixmap items
visible pages have their own annotation overlay
viewport scrolling decides which pages are loaded and which page is current
```

## Core Principle

Do not render the whole PDF at once.

Large PDFs may have hundreds of pages and thousands of annotations. Rendering all pages and all overlays at once would cause slow startup, high memory use, and poor interaction.

Use virtualization:

- Calculate layout metadata for all pages.
- Render only visible pages.
- Keep a small cache before and after the visible range.
- Release pixmaps and overlays that are far outside the viewport.
- Update the current page based on scroll position.

## Proposed Data Structures

```python
PageLayout
    page_index: int
    page_size: QSizeF
    page_rect_scene: QRectF
    y_top: float
    y_bottom: float

PageViewItem
    page_index: int
    pixmap_item: QGraphicsPixmapItem | None
    annotation_items: list[QGraphicsItem]
    is_rendered: bool
```

The scene coordinate for a PDF annotation becomes:

```python
scene_x = page_layout.page_rect_scene.x() + pdf_x * zoom
scene_y = page_layout.page_rect_scene.y() + pdf_y * zoom
```

So the current single-page conversion:

```python
scene_rect(model.rect)
```

needs to become page-aware:

```python
scene_rect(page_index, model.rect)
```

or be handled by a page-local renderer.

## Rendering Strategy

1. When opening a PDF or changing zoom, calculate vertical layout for all pages.
2. Set `QGraphicsScene.sceneRect` to the full document height.
3. Listen to viewport scroll changes.
4. Compute visible page indexes from viewport scene rect.
5. Render:
   - visible pages
   - plus 1-2 pages before and after as cache
6. Unload pages far outside the cache range:
   - remove page pixmap item
   - remove annotation overlay items
   - keep only `PageLayout` metadata

## Annotation Overlay

Continuous mode needs page-offset-aware overlay rendering.

Each annotation model is still stored in PDF page coordinates. During display:

```text
PDF page coordinates
-> scale by zoom
-> add page layout scene offset
-> draw overlay item
```

This affects:

- Highlight overlay
- FreeText overlay
- Square overlay
- Arrow overlay
- Hit testing
- Selection handles
- Dragging and resizing

## Affected Features

Continuous view will affect many existing workflows:

- Selecting annotations
- Dragging annotations
- Resizing FreeText and Square
- Moving Arrow endpoints
- Adding FreeText / Square / Arrow
- Precise Highlight text selection
- Annotation List current page behavior
- Quick Audit current page behavior
- Search result jump
- Batch FreeText result jump
- Page number input jump
- Back / Forward view history
- Zoom in/out
- Save and overlay refresh

Because of this, continuous view should not immediately replace the current single-page mode.

## Recommended Product Strategy

Keep two view modes:

```text
Single Page
Continuous
```

`Single Page` remains the stable full-feature mode.

`Continuous` starts as a read-only viewing mode, then gradually gains annotation overlay and editing support.

## Implementation Stages

### CPV-1: Continuous Page Layout Model

Add page layout metadata without changing the current single-page rendering.

Tasks:

- Add `PageLayout` model.
- Add continuous layout calculator.
- For each page, calculate:
  - page width
  - page height
  - scene x/y
  - scene rect
- Calculate total scene height.

Expected result:

- No visible behavior change yet.
- Layout metadata can be inspected/debugged.

### CPV-2: Read-Only Continuous Page Rendering

Add a `Continuous` view mode that renders page pixmaps only.

Tasks:

- Add view mode state:
  - `Single Page`
  - `Continuous`
- Add UI toggle or menu action.
- In continuous mode:
  - calculate layouts
  - render visible page pixmaps
  - unload distant page pixmaps
- Sync current page from scroll position.
- Support zoom in/out by recalculating layout.

Limitations:

- No annotation overlay in continuous mode.
- No annotation editing in continuous mode.

Expected result:

- User can continuously scroll through PDF pages.
- Single-page mode still has all current annotation features.

### CPV-3: Read-Only Annotation Overlay

Render annotations on visible pages in continuous mode.

Tasks:

- Make annotation renderer page-offset-aware.
- Render visible page annotations:
  - Highlight
  - FreeText
  - Square
  - Arrow
- Unload overlay items for distant pages.

Limitations:

- Overlay is read-only.
- No selection, drag, resize, or edit yet.

Expected result:

- Continuous mode visually matches current single-page annotation display.

### CPV-4: Selection And Jump Integration

Support selecting and navigating to annotations in continuous mode.

Tasks:

- Hit test annotation overlay items across pages.
- Select annotation in continuous mode.
- Center/scroll to annotation.
- Make these features continuous-aware:
  - Annotation List
  - Search result jump
  - Batch FreeText jump
  - Anchor jump
  - Page number jump
  - Back / Forward view history

Expected result:

- User can jump to annotations and select them in continuous mode.

### CPV-5: Editing In Continuous Mode

Add editing support after selection and overlay are stable.

Tasks:

- Drag FreeText / Square / Arrow.
- Resize FreeText / Square.
- Move Arrow endpoints.
- Edit properties from the Properties tab.
- Delete annotations.
- Add FreeText / Square / Arrow.
- Add Highlight after text selection is continuous-aware.

Expected result:

- Continuous mode becomes a full editing mode.

### CPV-6: Cache And Performance Hardening

Optimize for large PDFs.

Tasks:

- Tune visible page cache range.
- Track pixmap memory use.
- Release distant page items aggressively.
- Avoid unnecessary overlay rebuilds.
- Add debug timing logs:
  - layout
  - page render
  - annotation read
  - overlay render
  - unload
- Test with large PDFs:
  - hundreds of pages
  - thousands of annotations

Expected result:

- Continuous mode remains responsive with large documents.

## Recommended First Step

Start with:

```text
CPV-1 + CPV-2
```

Only implement read-only continuous page rendering first.

Do not enable annotation editing in continuous mode at the beginning.

This keeps risk low because the existing `Single Page` mode remains the stable path for all annotation editing features.

