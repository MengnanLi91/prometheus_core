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

import { useEffect, useMemo, useRef, useState } from "react";
import { ReactFlow, type Edge, type NodeTypes, type ReactFlowInstance } from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import {
  WorkflowAgentNode,
  WorkflowCheckpointNode,
  type AgentNode,
  type CheckpointNode,
} from "./WorkflowGraphNodes";

export type WorkflowGraphNode = AgentNode | CheckpointNode;

export interface WorkflowGraphWire {
  from: string;
  to: string;
}

interface WorkflowGraphProps {
  nodes: WorkflowGraphNode[];
  wires: WorkflowGraphWire[];
  /** Layout only (size, margins). The graph fits itself to this box. */
  className?: string;
}

const NODE_TYPES: NodeTypes = {
  agent: WorkflowAgentNode,
  checkpoint: WorkflowCheckpointNode,
};

const FIT_VIEW_OPTIONS = { padding: 0.08 };

/**
 * Workflow DAG (Figma D.1, WF-1) on React Flow: agent nodes and checkpoints
 * joined by curved wires. Display only, so dragging, connecting, selecting,
 * zooming and panning are all off.
 */
export function WorkflowGraph({ nodes, wires, className = "" }: WorkflowGraphProps) {
  const edges = useMemo<Edge[]>(
    () => wires.map(({ from, to }) => ({ id: `${from}->${to}`, source: from, target: to })),
    [wires],
  );

  // fitView only runs on load, so refit when the box changes size (window
  // resize, sidebar toggle) or the graph would be left clipped.
  const boxRef = useRef<HTMLDivElement>(null);
  const [flow, setFlow] = useState<ReactFlowInstance | null>(null);
  useEffect(() => {
    const box = boxRef.current;
    if (!box || !flow) return;
    const observer = new ResizeObserver(() => void flow.fitView(FIT_VIEW_OPTIONS));
    observer.observe(box);
    return () => observer.disconnect();
  }, [flow]);

  return (
    // React Flow reads its colours from these variables, so the wires and its
    // credit (kept, as React Flow asks of commercial users) follow the theme.
    <div
      ref={boxRef}
      className={`rounded-md border border-base-content/10 bg-base-300 [--xy-attribution-background-color:transparent] [--xy-edge-stroke-width:1.5] [--xy-edge-stroke:var(--color-primary)] [&_.react-flow\\_\\_attribution_a]:text-base-content/40 ${className}`}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onInit={setFlow}
        // React Flow stops zooming out at 0.5, which clips the graph in narrow boxes.
        minZoom={0.1}
        fitView
        fitViewOptions={FIT_VIEW_OPTIONS}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        panOnDrag={false}
        panOnScroll={false}
        zoomOnScroll={false}
        zoomOnPinch={false}
        zoomOnDoubleClick={false}
        preventScrolling={false}
      />
    </div>
  );
}
