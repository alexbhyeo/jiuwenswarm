// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

// Workaround for a gap in the published @a2ui/react package's Image
// component (still present as of 0.10.2): the "smallFeature" variant only
// sets maxWidth (no height), and "mediumFeature" - a valid, schema-declared
// variant - isn't handled by the renderer's if/else chain at all, so it
// gets no sizing whatsoever. object-fit only has a visible effect once both
// box dimensions are fixed; without a height, an <img> just scales to its
// own natural aspect ratio at the given width, so a grid of photos with
// different native aspect ratios never reads as a uniform grid of "frames"
// regardless of what fit value the content specifies. Every feature variant
// here fills the full width of whatever it's placed in (a Card's normal
// padding is what keeps it from touching the card's border, not a negative
// margin) - smallFeature uses aspect-ratio so it stays a square at any
// width; mediumFeature/header use a fixed height, matching the pattern
// already established for "header".
import { useState } from 'react';
import { ImageApi } from '@a2ui/web_core/v0_9/basic_catalog';
import { createComponentImplementation } from '@a2ui/react/v0_9';

function getWeightStyle(weight: unknown): Record<string, string | number> {
  return typeof weight === 'number' ? { flex: `${weight}`, minWidth: 0, minHeight: 0 } : {};
}

function mapFit(fit: unknown): string {
  if (fit === 'scaleDown') return 'scale-down';
  return typeof fit === 'string' && fit ? fit : 'fill';
}

// Wikimedia Commons' on-the-fly thumbnail service
// (.../thumb/x/xx/File.jpg/NNNpx-File.jpg) rejects every width we've tried
// from this network with "400 Use thumbnail sizes listed on
// https://w.wiki/GHai", while the direct, full-resolution file URL
// (.../commons/x/xx/File.jpg, no /thumb/ segment) loads fine - confirmed via
// curl and Python requests independent of this app. The model reliably picks
// the standard thumb URL pattern (it's the normal way to reference a sized
// Commons image), so rather than depend on prompt changes, strip the broken
// /thumb/ + size-prefix segment here and fall back to the working original.
const WIKIMEDIA_THUMB_PATTERN =
  /^(https?:\/\/upload\.wikimedia\.org\/[^/]+\/[^/]+)\/thumb\/(.+\/[^/]+\.\w+)\/[^/]+$/i;

function resolveImageUrl(url: unknown): string {
  if (typeof url !== 'string') return '';
  const match = url.match(WIKIMEDIA_THUMB_PATTERN);
  return match ? `${match[1]}/${match[2]}` : url;
}

export const ImageWithStyles = createComponentImplementation(ImageApi, ({ props }) => {
  const [loadFailed, setLoadFailed] = useState(false);
  const style: Record<string, string | number> = {
    boxSizing: 'border-box',
    ...getWeightStyle(props.weight),
    objectFit: mapFit(props.fit),
    display: 'block',
    borderRadius: 'var(--a2ui-image-border-radius, 0)',
  };

  if (props.variant === 'icon') {
    style.width = 'var(--a2ui-image-icon-size, 24px)';
    style.height = 'var(--a2ui-image-icon-size, 24px)';
  } else if (props.variant === 'avatar') {
    style.width = 'var(--a2ui-image-avatar-size, 40px)';
    style.height = 'var(--a2ui-image-avatar-size, 40px)';
    style.borderRadius = '50%';
  } else if (props.variant === 'smallFeature') {
    style.width = '100%';
    style.aspectRatio = '1';
    style.height = 'auto';
  } else if (props.variant === 'mediumFeature') {
    style.width = '100%';
    style.height = 'var(--a2ui-image-medium-feature-size, 260px)';
  } else if (props.variant === 'largeFeature') {
    style.width = '100%';
    style.maxHeight = 'var(--a2ui-image-large-feature-size, 400px)';
  } else if (props.variant === 'header') {
    style.width = '100%';
    style.height = 'var(--a2ui-image-header-size, 200px)';
    style.objectFit = 'cover';
  }

  const resolvedUrl = resolveImageUrl(props.url);

  // A blank imageUrl (the model couldn't find a real photo for this item -
  // common in data-bound list templates where one shared Image can't be
  // conditionally omitted per item) previously rendered as an empty <img
  // src="">, which browsers just leave blank with no broken-icon, reading
  // as an unstyled layout glitch. A URL that resolves but 404s/times out at
  // runtime looks the same way once the browser's default broken-image icon
  // renders at native size instead of filling this box. Show the same
  // deliberate "no image" placeholder for both cases instead.
  if (!resolvedUrl || loadFailed) {
    // icon/avatar are 24-40px - too small for a label, just the glyph.
    const isTiny = props.variant === 'icon' || props.variant === 'avatar';
    const placeholderStyle: Record<string, string | number> = {
      ...style,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'var(--a2ui-image-placeholder-background, light-dark(#eee, #333))',
      color: 'var(--a2ui-image-placeholder-color, light-dark(#999, #777))',
      fontSize: 'var(--a2ui-font-size-xs, 0.75rem)',
      gap: '4px',
      flexDirection: 'column',
    };
    delete placeholderStyle.objectFit;
    return (
      <div style={placeholderStyle} role="img" aria-label={props.description || 'No image available'}>
        <span aria-hidden="true" style={{ fontSize: isTiny ? '1em' : '1.5em' }}>🖼️</span>
        {!isTiny && <span>No image available</span>}
      </div>
    );
  }

  return (
    <img
      src={resolvedUrl}
      alt={props.description || ''}
      style={style}
      onError={() => setLoadFailed(true)}
    />
  );
});
