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

import { usePageHeader } from "@/lib/store/pageHeader";
import { SectionLabel } from "../ui/SectionLabel";
import { WorkflowTemplateCard } from "./WorkflowTemplateCard";
import { SELECTED_TEMPLATE_ID, WORKFLOW_TEMPLATES } from "./mockData";

/**
 * Workflows landing page (Figma D.1): the template picker, then the execution
 * planner for the selected template. Display only for now; selection is fixed
 * to the example workflow.
 */
export function WorkflowConsole() {
  usePageHeader({ title: "Workflow Console" });

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-y-auto p-6">
      <section className="flex flex-col gap-3">
        <SectionLabel>Workflow Templates</SectionLabel>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {WORKFLOW_TEMPLATES.map((template) => (
            <WorkflowTemplateCard
              key={template.id}
              template={template}
              selected={template.id === SELECTED_TEMPLATE_ID}
            />
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <SectionLabel>Workflow Execution Planner</SectionLabel>
        {/* TODO(PPLT-211): graph and plan summary. */}
      </section>
    </div>
  );
}
