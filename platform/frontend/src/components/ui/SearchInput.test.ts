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
import { createRequire, register } from "node:module";
import test from "node:test";
import { pathToFileURL } from "node:url";
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

/* The component, compiled from .tsx by esbuild (already installed through Vite).
 * A loader hook is needed because type stripping does not run JSX. The hook is a data: URL,
 * which cannot resolve a bare specifier, so it imports esbuild by its absolute file URL. */
const ESBUILD = pathToFileURL(createRequire(import.meta.url).resolve("esbuild")).href;
const hooks = `
import { transform } from ${JSON.stringify(ESBUILD)};
export async function load(url, context, next) {
  if (!url.endsWith(".tsx")) return next(url, context);
  const loaded = await next(url, { ...context, format: "module" });
  const { code } = await transform(String(loaded.source), { loader: "tsx", jsx: "automatic", format: "esm" });
  return { format: "module", shortCircuit: true, source: code };
}
`;
register("data:text/javascript," + encodeURIComponent(hooks));

const { SearchInput } = await import("./SearchInput.tsx");

type Element = ReactElement<Record<string, unknown>>;
/** The <input> element that SearchInput returns inside its <label>. */
const inputOf = (label: Element) => (label.props.children as Element[])[1];

test("SearchInput renders the icon, placeholder and aria-label, uncontrolled without value", () => {
  const html = renderToStaticMarkup(SearchInput({ placeholder: "Search files", label: "Search the files" }));

  // lucide-react 0.292 marks no icon name, so match the Search glyph: a lens circle and its handle.
  assert.match(html, /<svg[^>]*aria-hidden="true"><circle cx="11" cy="11" r="8"><\/circle><path d="m21 21-4.3-4.3">/);
  assert.match(html, /placeholder="Search files"/);
  assert.match(html, /aria-label="Search the files"/);
  assert.doesNotMatch(html, /value=/);
});

test("SearchInput renders a given value and reports the new text through onChange", () => {
  const seen: string[] = [];
  const label = SearchInput({ placeholder: "p", label: "l", value: "rea", onChange: (v) => seen.push(v) });

  assert.match(renderToStaticMarkup(label), /value="rea"/);
  const onChange = inputOf(label).props.onChange as (event: unknown) => void;
  onChange({ target: { value: "reactor" } });
  assert.deepEqual(seen, ["reactor"]);
});

// Never called: tsc fails here if a controlled value stops requiring onChange.
void (() =>
  // @ts-expect-error value requires onChange
  SearchInput({ placeholder: "p", label: "l", value: "x" }));
