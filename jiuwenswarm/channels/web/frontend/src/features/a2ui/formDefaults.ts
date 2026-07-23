// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

type UnknownRecord = Record<string, unknown>;
// Kept local rather than importing the SDK's Signal-backed DataValue type:
// this module only needs "whatever setValue accepts", not the full runtime type.
type DataValue = unknown;

export function isDevA2UIDiagnosticEnabled(): boolean {
  return Boolean(import.meta.env?.DEV);
}

export function a2uiDebug(message: string, data?: unknown): void {
  if (isDevA2UIDiagnosticEnabled()) {
    console.debug(message, data);
  }
}

export function a2uiWarn(message: string, data?: unknown): void {
  if (isDevA2UIDiagnosticEnabled()) {
    console.warn(message, data);
  }
}

export function a2uiError(message: string, data?: unknown): void {
  if (isDevA2UIDiagnosticEnabled()) {
    console.error(message, data);
  }
}

export function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function normalizeA2UIPath(path: unknown): string | null {
  if (typeof path !== 'string' || !path.trim()) {
    return null;
  }
  const trimmed = path.trim();
  return trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
}

export function leafA2UIPath(path: string): string | null {
  const segments = path.split('/').filter(Boolean);
  if (segments.length <= 1 || !path.startsWith('/')) {
    return null;
  }
  return `/${segments[segments.length - 1]}`;
}

export function a2uiPathCandidates(path: string): string[] {
  const normalized = normalizeA2UIPath(path);
  if (!normalized) {
    return [];
  }
  const leafPath = leafA2UIPath(normalized);
  return leafPath && leafPath !== normalized ? [normalized, leafPath] : [normalized];
}

export function dualWriteA2UIValue(
  setValue: (path: string, value: DataValue) => void,
  path: string,
  value: DataValue,
): void {
  const candidates = a2uiPathCandidates(path);
  for (const candidate of candidates) {
    setValue(candidate, value);
  }
}

export function shouldFillEmptyActionValue(value: unknown): boolean {
  if (value === undefined || value === null) {
    return true;
  }
  if (Array.isArray(value)) {
    return value.length === 0;
  }
  if (isRecord(value)) {
    return Object.keys(value).length === 0;
  }
  return false;
}

// A2UI 0.9.1's ChoicePicker.value is a DynamicStringList: either a literal
// array of strings directly, or a DataBinding ({path: string}). There is no
// more "{literalArray: [...]}" wrapper object (that was 0.8's shape).
export function literalArrayValues(value: unknown): unknown[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item) => item !== null && item !== undefined);
}

export function optionDefaultValue(options: unknown): unknown {
  if (!Array.isArray(options)) {
    return null;
  }
  for (const option of options) {
    if (isRecord(option) && option.value !== undefined && option.value !== null) {
      return option.value;
    }
  }
  return null;
}

// A2UI 0.9.1's ChoicePicker has an explicit variant field
// ("mutuallyExclusive" | "multipleSelection", default "mutuallyExclusive") -
// no more inferring single- vs multi-select from a chips/checkbox display
// hint or a maxAllowedSelections count (0.8's heuristic, and the root cause
// of a chip-selection bug that made picking a new option silently no-op
// once one was already selected).
export function isMultiSelectChoice(props: UnknownRecord): boolean {
  return props.variant === 'multipleSelection';
}

export function visibleChoiceDefault(props: UnknownRecord): unknown {
  const literalDefaults = literalArrayValues(props.value);
  if (isMultiSelectChoice(props)) {
    return literalDefaults.length > 0 ? literalDefaults : null;
  }
  const optionDefault = optionDefaultValue(props.options);
  if (optionDefault !== null) {
    return [optionDefault];
  }
  return literalDefaults.length > 0 ? literalDefaults : null;
}

export function a2uiScalarText(value: unknown): string | null {
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }

  if (value instanceof Map) {
    const scalarValues = Array.from(value.values())
      .map((item) => a2uiScalarText(item))
      .filter((item): item is string => item !== null);
    return scalarValues.length === 1 ? scalarValues[0] : null;
  }

  if (Array.isArray(value)) {
    return value.length === 1 ? a2uiScalarText(value[0]) : null;
  }

  if (isRecord(value)) {
    const scalarValues = Object.values(value)
      .map((item) => a2uiScalarText(item))
      .filter((item): item is string => item !== null);
    return scalarValues.length === 1 ? scalarValues[0] : null;
  }

  return null;
}

// A2UI 0.9.1's DynamicString is a literal string/number/boolean directly, or
// a DataBinding ({path: string}) - no more "{literalString: ...}" wrapper.
export function resolveA2UITextValue(
  value: unknown,
  getValue: (path: string) => unknown,
): string | null {
  if (isRecord(value)) {
    if (typeof value.path === 'string') {
      return a2uiScalarText(getValue(value.path));
    }
    return null;
  }
  return a2uiScalarText(value);
}
