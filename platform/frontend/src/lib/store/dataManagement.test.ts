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

import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

/* The real Data Management store, with React stubbed out as in chat.test.ts.
 * Outside a render the real useSyncExternalStore has no dispatcher, so the stub
 * returns the store's snapshot directly. */
const hooks = `
export async function resolve(specifier, context, next) {
  if (specifier === "react") return { url: "stub:react", shortCircuit: true };
  return next(specifier, context);
}
export async function load(url, context, next) {
  if (url === "stub:react") {
    return {
      format: "module",
      shortCircuit: true,
      source: "export function useSyncExternalStore(sub, get) { return get(); }",
    };
  }
  return next(url, context);
}
`;
register("data:text/javascript," + encodeURIComponent(hooks));

const { useDataManagement, derive, storagePercent, formatBytes } = await import("./dataManagement.ts");

test("useDataManagement returns ready state with six sources and six artifacts", () => {
  const state = useDataManagement(); // not a render: the stubbed hook returns the snapshot
  assert.equal(state.status, "ready");
  assert.equal(state.sources.length, 6);
  const disabled = state.sources.filter((s) => !s.enabled);
  assert.deepEqual(
    disabled.map((s) => s.name),
    ["Reactor Operating Logs"],
  );
  assert.equal(state.artifacts.length, 6);
  for (const a of state.artifacts) {
    assert.ok(a.name.length > 0, `artifact ${a.id} has an empty name`);
    assert.ok(["report", "mesh", "result"].includes(a.type), `artifact ${a.id} has type ${a.type}`);
  }
});

test("derive returns empty for no sources and no artifacts, else ready", () => {
  const { sources } = useDataManagement();
  assert.equal(derive([], []), "empty");
  assert.equal(derive(sources.slice(0, 1), []), "ready");
});

test("storagePercent rounds used over total to a whole percentage", () => {
  assert.equal(storagePercent({ used_bytes: 1.8e12, total_bytes: 5e12 }), 36);
  assert.equal(storagePercent({ used_bytes: 1, total_bytes: 3 }), 33);
});

test("formatBytes steps to the decimal unit with at most one decimal", () => {
  assert.equal(formatBytes(1.8e12), "1.8 TB");
  assert.equal(formatBytes(890e9), "890 GB");
  assert.equal(formatBytes(84e3), "84 KB");
});
