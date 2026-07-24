// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

// A2UI v0.9.1's React renderer replaced v0.8's context-based model
// (<A2UIProvider onAction={...}> + useA2UIActions()'s processMessages(), with
// <A2UIRenderer surfaceId={id} registry={...}/> pulling from that same
// context by id) with an explicit, non-context one: a single MessageProcessor
// instance you construct directly, feed processMessages(...) into, and read
// SurfaceModel objects back out of via onSurfaceCreated - then hand that
// exact surface object to <A2uiSurface surface={surface}/> for rendering.
//
// Messages are namespaced per chat message (see a2uiContent.ts's
// namespaceA2UIMessages), so every surfaceId is already globally unique -
// one processor instance for the whole app's lifetime is enough, mirroring
// the single ComponentRegistry.getInstance() singleton v0.8 used.

import { Catalog, MessageProcessor } from '@a2ui/web_core/v0_9';
import type { A2uiClientAction, A2uiMessage, SurfaceModel } from '@a2ui/web_core/v0_9';
import { basicCatalog, type ReactComponentImplementation } from '@a2ui/react/v0_9';
import { dispatchA2UIAction } from './actionBridge';
import { ButtonWithStyles } from './ButtonWithStyles';
import { TextFieldWithStyles } from './TextFieldWithStyles';
import { ChoicePickerWithStyles } from './ChoicePickerWithStyles';
import { ImageWithStyles } from './ImageWithStyles';
import type { ServerToClientMessage } from './a2uiContent';

const surfacesById = new Map<string, SurfaceModel<ReactComponentImplementation>>();
const surfaceListeners = new Set<(surfaceId: string) => void>();

// @a2ui/react's basicCatalog ships Button/TextField/ChoicePicker with two
// confirmed bugs (still present as of 0.10.2): (1) their CSS-module class
// maps compile to empty objects, so className ends up as the literal string
// "undefined" and these components render with zero visual styling (no
// spacing/borders/selected-state - ChoicePicker options visually merge into
// one unreadable, effectively unclickable block); (2) even the package's own
// real CSS for these components (plain, unhashed selectors like
// .button/.chip/.host, in v0_9/index.css) is never imported by the package's
// own JS and isn't in its exports map, so it can never reach our bundle
// either - see a2ui.css for a verbatim copy of those rules. Rather than
// design new styling, swap in overrides that apply those exact same
// selectors correctly; Row/Column/Card/Divider are unaffected since they use
// inline styles instead of this class map, so they're kept as-is.
//
// Image has a separate, unrelated gap (also still present as of 0.10.2):
// its "smallFeature" variant only sets maxWidth (no height), and
// "mediumFeature" - a valid, schema-declared variant - isn't handled at all,
// so object-fit never has a real box to crop within and photo grids render
// with inconsistent, source-image-dependent thumbnail shapes. ImageWithStyles
// adds the missing sizing via aspect-ratio.
const patchedComponents = Array.from(basicCatalog.components.values()).map((component) => {
  if (component.name === 'Button') return ButtonWithStyles;
  if (component.name === 'TextField') return TextFieldWithStyles;
  if (component.name === 'ChoicePicker') return ChoicePickerWithStyles;
  if (component.name === 'Image') return ImageWithStyles;
  return component;
});
const patchedBasicCatalog = new Catalog<ReactComponentImplementation>(
  basicCatalog.id,
  patchedComponents,
  Array.from(basicCatalog.functions.values()),
  basicCatalog.themeSchema
);

export const messageProcessor = new MessageProcessor<ReactComponentImplementation>(
  [patchedBasicCatalog],
  (action: A2uiClientAction) => dispatchA2UIAction(action),
);

messageProcessor.onSurfaceCreated((surface) => {
  surfacesById.set(surface.id, surface);
  for (const listener of surfaceListeners) {
    listener(surface.id);
  }
});

messageProcessor.onSurfaceDeleted((surfaceId) => {
  surfacesById.delete(surfaceId);
});

// The backend's a2ui-agent-sdk (Python) reports its bundled basic catalog's
// id as "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"
// (this is what the system prompt teaches the model to copy into
// createSurface.catalogId) - but @a2ui/web_core's own basicCatalog.id is the
// differently-shaped "https://a2ui.org/specification/v0_9/basic_catalog.json".
// MessageProcessor.processCreateSurfaceMessage does a strict `catalogs.find(c
// => c.id === catalogId)` with no fallback, so a model that faithfully uses
// the id our own prompt taught it throws "Catalog not found" - silently
// killing the whole surface (caught by A2UIMessageContent's try/catch and
// only logged, never shown to the user). Since we only ever register one
// catalog, whatever catalogId the model sends is unambiguous - normalize it
// to the frontend's actual id before it reaches the SDK.
function normalizeCreateSurfaceCatalogId(messages: ServerToClientMessage[]): ServerToClientMessage[] {
  return messages.map((message) => {
    if (!message.createSurface) {
      return message;
    }
    return {
      ...message,
      createSurface: { ...message.createSurface, catalogId: basicCatalog.id },
    };
  });
}

/**
 * ServerToClientMessage is a loosely-typed structural shape used for manual
 * JSON parsing (see a2uiContent.ts); each parsed message only ever carries
 * one of its four optional keys, satisfying A2uiMessage's discriminated
 * union at runtime even though the two types aren't directly assignable.
 */
export function processA2UIMessages(messages: ServerToClientMessage[]): void {
  messageProcessor.processMessages(
    normalizeCreateSurfaceCatalogId(messages) as unknown as A2uiMessage[]
  );
}

export function getA2UISurface(
  surfaceId: string
): SurfaceModel<ReactComponentImplementation> | undefined {
  return surfacesById.get(surfaceId);
}

/** Subscribe to newly-created surfaces (fires with the surfaceId). Returns an unsubscribe function. */
export function onA2UISurfaceCreated(listener: (surfaceId: string) => void): () => void {
  surfaceListeners.add(listener);
  return () => {
    surfaceListeners.delete(listener);
  };
}
