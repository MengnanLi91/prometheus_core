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

import type { ComponentProps } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { StatusPill } from "../ui/StatusPill";

export type WorkflowStepStatus = "done" | "queued" | "blocked";

const STATUS_PILL: Record<WorkflowStepStatus, ComponentProps<typeof StatusPill>> = {
  done: { label: "Done", tone: "success" },
  queued: { label: "Queued", tone: "warning" },
  blocked: { label: "Blocked", tone: "neutral" },
};

export interface AgentNodeData extends Record<string, unknown> {
  name: string;
  status: WorkflowStepStatus;
  input: string;
  output: string;
  /** Display string, e.g. "40s". */
  est: string;
  /** Node width in px; D.1 sizes nodes to their content. */
  width: number;
}

export interface CheckpointNodeData extends Record<string, unknown> {
  name: string;
}

export type AgentNode = Node<AgentNodeData, "agent">;
export type CheckpointNode = Node<CheckpointNodeData, "checkpoint">;

/** Wires attach at the top and bottom centre. The handles are only anchors, so they're hidden. */
const HANDLE_CLASS = "!size-px !min-h-0 !min-w-0 !border-0 !bg-transparent";

/** Sizes go on each span: the base layer sets span font-size, so it isn't inherited. */
function PortRow({ label, value, dot }: { label: "IN" | "OUT"; value: string; dot: string }) {
  const dotEl = <span aria-hidden="true" className={`size-2 shrink-0 rounded-full ${dot}`} />;
  return (
    <div className="flex min-w-0 items-center gap-1.5 font-mono">
      {label === "IN" && dotEl}
      <span className="shrink-0 text-[8.5px] leading-3 text-base-content/50">{label}</span>
      <span className="min-w-0 flex-1 truncate text-[9px] leading-3 text-base-content/70">{value}</span>
      {label === "OUT" && dotEl}
    </div>
  );
}

/** One agent step (Figma D.1): name and status, input and output ports, and estimated time. */
export function WorkflowAgentNode({ data }: NodeProps<AgentNode>) {
  return (
    <div
      className="flex flex-col gap-2 rounded-lg border border-base-content/15 bg-base-100 px-3 py-2.5"
      style={{ width: data.width }}
    >
      <Handle type="target" position={Position.Top} isConnectable={false} className={HANDLE_CLASS} />
      <div className="flex min-w-0 items-center justify-between gap-1.5">
        <span className="min-w-0 truncate text-xs font-semibold leading-4 text-base-content" title={data.name}>
          {data.name}
        </span>
        <StatusPill {...STATUS_PILL[data.status]} />
      </div>
      <PortRow label="IN" value={data.input} dot="bg-info" />
      <PortRow label="OUT" value={data.output} dot="bg-success" />
      <div className="flex items-center justify-between gap-2 font-mono">
        <span className="text-[8.5px] leading-3 text-base-content/50">EST</span>
        <span className="text-[9px] font-medium leading-3 text-base-content/70">{data.est}</span>
      </div>
      <Handle type="source" position={Position.Bottom} isConnectable={false} className={HANDLE_CLASS} />
    </div>
  );
}

/**
 * A review checkpoint (Figma D.1): a diamond, with its name to the right.
 * The box is sized so the diamond's rounded tips touch its top and bottom
 * edges, where the wires attach: a 32px square turned 45° with 4px corners
 * reaches 21px from its centre.
 */
export function WorkflowCheckpointNode({ data }: NodeProps<CheckpointNode>) {
  return (
    <div className="relative flex size-[42px] items-center justify-center">
      <Handle type="target" position={Position.Top} isConnectable={false} className={HANDLE_CLASS} />
      <span aria-hidden="true" className="size-8 shrink-0 rotate-45 rounded border-[1.5px] border-primary bg-primary/20" />
      <div className="absolute left-full top-1/2 ml-2 flex -translate-y-1/2 flex-col whitespace-nowrap font-mono uppercase">
        <span className="text-[9px] font-medium leading-3 tracking-[0.03em] text-base-content">{data.name}</span>
        <span className="text-[7.5px] leading-3 tracking-[0.04em] text-base-content/50">Checkpoint</span>
      </div>
      <Handle type="source" position={Position.Bottom} isConnectable={false} className={HANDLE_CLASS} />
    </div>
  );
}
