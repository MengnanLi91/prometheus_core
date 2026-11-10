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

/* The controls overlay is 288px wide; in a narrow viewer (the side panel) it
 * would hide most of the mesh, so it starts collapsed there. */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import MeshViewerControls from "./MeshViewerControls.tsx";
import { controlsStartOpen } from "./controlsLayout.ts";

const props = {
  numTimesteps: 2,
  currentTimestep: 0,
  onTimestepChange: () => {},
  isPlaying: false,
  onTogglePlay: () => {},
  variableOptions: [{ value: "point:T", label: "T", group: "point" as const }],
  currentVariable: "T",
  onVariableChange: () => {},
  colorPreset: "coolToWarm" as const,
  onColorPresetChange: () => {},
  scalarRange: [0, 1] as [number, number],
  onScalarRangeChange: () => {},
};

test("the controls start open by default", () => {
  render(<MeshViewerControls {...props} />);
  expect(screen.getByRole("button", { name: "Collapse controls panel" })).toBeTruthy();
});

test("collapsed controls start as a button that opens them", async () => {
  const user = userEvent.setup();
  render(<MeshViewerControls {...props} defaultOpen={false} />);

  expect(screen.queryByRole("button", { name: "Collapse controls panel" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Open viewer controls" }));
  expect(screen.getByRole("button", { name: "Collapse controls panel" })).toBeTruthy();
});

test("the controls start collapsed only in a narrow viewer", () => {
  expect(controlsStartOpen(380)).toBe(false); // the side panel
  expect(controlsStartOpen(639)).toBe(false);
  expect(controlsStartOpen(640)).toBe(true);
  expect(controlsStartOpen(900)).toBe(true); // the chat
  // Not measured yet (no layout, e.g. jsdom): keep today's behavior.
  expect(controlsStartOpen(0)).toBe(true);
});
