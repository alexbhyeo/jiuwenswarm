// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

// v0.9.1 has no per-version ComponentRegistry/A2UIRenderer to look up by
// protocol version string - rendering a surface means holding the actual
// SurfaceModel object (from the message-processor bridge) and handing it to
// <A2uiSurface surface={surface}/>. Since createSurface is processed
// synchronously as part of processMessages() in the common case, but nothing
// guarantees that ordering, this subscribes to onA2UISurfaceCreated and
// re-renders once the surface actually shows up.

import { useEffect, useState } from 'react';
import { A2uiSurface } from '@a2ui/react/v0_9';
import { getA2UISurface, onA2UISurfaceCreated } from './messageProcessor';

export interface A2UIRendererProps {
  surfaceId: string;
}

export function A2UISurfaceRenderer({ surfaceId }: A2UIRendererProps) {
  const [, bumpVersion] = useState(0);

  useEffect(() => {
    if (getA2UISurface(surfaceId)) {
      return undefined;
    }
    const unsubscribe = onA2UISurfaceCreated((createdId) => {
      if (createdId === surfaceId) {
        bumpVersion((value) => value + 1);
      }
    });
    return unsubscribe;
  }, [surfaceId]);

  const surface = getA2UISurface(surfaceId);
  if (!surface) {
    return null;
  }
  return <A2uiSurface surface={surface} />;
}
