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

// Dropdown menu types
export interface MenuItem {
  label: string;
  shortcut?: string | null;
  identifier: string;
  selectValue: boolean | null;
  subList: DropdownMenuItem[] | null;
  onClick?: () => void;
  disabled?: boolean;
}

export interface DropdownMenuItem {
  type: 'normal' | 'check' | 'radio';
  list: MenuItem[];
}
