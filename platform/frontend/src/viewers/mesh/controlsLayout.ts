// This file is part of Prometheus
// https://inl.gov/super-intelligence/prometheus/
//
// Copyright 2026 Battelle Energy Alliance, LLC
// All rights reserved, see NOTICE.txt for full restrictions
// https://github.com/idaholab/prometheus_core/blob/main/NOTICE.txt
//
// Licensed under the MIT License, please see LICENSE for details
// https://github.com/idaholab/prometheus_core/blob/main/LICENSE
//
// SPDX-License-Identifier: MIT

/** Viewers narrower than this start with the controls collapsed: the open box is
 * 288px (`w-72` in MeshViewerControls), so below about twice that it hides most
 * of the mesh. The side panel is about 380px wide. */
const NARROW_VIEWER_PX = 640;

/** Whether the mesh viewer's controls start open in a viewer `width` px wide. An
 * unmeasured width (0, no layout yet) keeps them open. */
export function controlsStartOpen(width: number): boolean {
  return width <= 0 || width >= NARROW_VIEWER_PX;
}
