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

import { Diamond, Zap } from "lucide-react";
import { Button } from "../ui/Button";

export type WorkflowComplexity = "complex" | "moderate" | "simple";

/** Badge colours from D.1. COMPLEX borrows the error token for its red: Figma
 * notes it isn't an error state, and a non-error colour is still to be picked. */
const COMPLEXITY_CLASSES: Record<WorkflowComplexity, string> = {
  complex: "border-error bg-error/10 text-error",
  moderate: "border-primary bg-primary/10 text-primary",
  simple: "border-info bg-info/10 text-info",
};

export interface WorkflowTemplate {
  id: string;
  name: string;
  complexity: WorkflowComplexity;
  checkpoints: number;
  agents: number;
  /** Display string, e.g. "1h 30m". */
  runtime: string;
  /** Speed-up over the manual baseline, e.g. "4.2x". */
  speedup: string;
}

interface WorkflowTemplateCardProps {
  template: WorkflowTemplate;
  selected?: boolean;
  onSelect?: () => void;
}

/**
 * A workflow template on the Workflow Console (Figma D.1): name, complexity and
 * checkpoint count, agent and runtime figures, the speed-up strip, and a
 * select button. The card itself isn't a click target; only the button is.
 */
export function WorkflowTemplateCard({ template, selected = false, onSelect }: WorkflowTemplateCardProps) {
  const { name, complexity, checkpoints, agents, runtime, speedup } = template;

  return (
    <article
      className={`card rounded-lg bg-base-200 ${
        selected ? "border-[1.5px] border-primary" : "border border-base-content/10"
      }`}
    >
      <div className="card-body min-w-0 gap-3 p-4">
        <div className="flex min-w-0 flex-col gap-1">
          <h3 className="m-0 truncate text-sm font-bold text-base-content" title={name}>
            {name}
          </h3>
          <div className="flex flex-wrap items-center gap-2 text-[10px] uppercase leading-3">
            <span className={`rounded border px-2 py-0.5 ${COMPLEXITY_CLASSES[complexity]}`}>{complexity}</span>
            <span className="inline-flex items-center gap-1 font-bold text-primary">
              <Diamond aria-hidden="true" size={9} className="fill-current" />
              {checkpoints} {checkpoints === 1 ? "checkpoint" : "checkpoints"}
            </span>
          </div>
        </div>

        <dl className="m-0 flex flex-col gap-1 text-[11px]">
          <div className="flex justify-between gap-2">
            <dt className="text-base-content/60">Agents Assigned</dt>
            <dd className="m-0 text-base-content">
              {agents} {agents === 1 ? "Agent" : "Agents"}
            </dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt className="text-base-content/60">Est. Runtime</dt>
            <dd className="m-0 text-base-content">{runtime}</dd>
          </div>
        </dl>

        <div className="flex items-center justify-between gap-2 rounded bg-base-content/5 px-3 py-2 text-[10px]">
          <span className="inline-flex items-center gap-1 text-info">
            <Zap aria-hidden="true" size={10} className="fill-current text-warning" />
            {speedup} faster
          </span>
          <span className="text-base-content/40">vs baseline</span>
        </div>

        {selected ? (
          <Button label="Selected" variant="primary" size="xs" wide onClick={onSelect} />
        ) : (
          <Button label="Select Template" outline size="xs" wide onClick={onSelect} />
        )}
      </div>
    </article>
  );
}
