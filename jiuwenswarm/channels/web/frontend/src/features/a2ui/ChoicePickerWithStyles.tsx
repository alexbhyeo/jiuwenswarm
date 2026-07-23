// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

// Workaround for two confirmed bugs in the published @a2ui/react package
// (still present as of 0.10.2): (1) ChoicePicker's own CSS-module class map
// compiles to an empty object (`var ChoicePicker_default = {}`), so every
// option's className ends up as the literal string "undefined" (or
// "undefined undefined" when selected); (2) even the real, unhashed
// selectors (.host/.label/.filterInput/.options/.chips/.chip/.selected/
// .optionLabel/.optionText) that exist in the same package's shipped
// v0_9/index.css are never imported by the package's own JS and aren't in
// its exports map, so that CSS can never reach our bundle either. With no
// className or styling, chips and checkbox options visually merge into one
// unreadable, effectively unclickable block of text - exactly the reported
// bug. This override applies the literal class names correctly; a2ui.css
// supplies a verbatim copy of the matching CSS rules.
import { useState } from 'react';
import { ChoicePickerApi } from '@a2ui/web_core/v0_9/basic_catalog';
import { createComponentImplementation } from '@a2ui/react/v0_9';

// The Generic Binder resolves each option's DynamicString `label` to a plain
// string before this component ever renders (confirmed by the stock
// ChoicePicker's own JS, which renders `opt.label` directly) - the .d.ts
// type still reflects the raw pre-binding schema shape, so this narrows for
// TypeScript only, not for a real runtime case.
function optionLabelText(label: unknown): string {
  return typeof label === 'string' ? label : String(label);
}

export const ChoicePickerWithStyles = createComponentImplementation(ChoicePickerApi, ({ props, context }) => {
  const [filter, setFilter] = useState('');
  const values = Array.isArray(props.value) ? props.value : [];
  const isMutuallyExclusive = props.variant === 'mutuallyExclusive';

  const onToggle = (val: string) => {
    if (isMutuallyExclusive) {
      props.setValue([val]);
    } else {
      const newValues = values.includes(val) ? values.filter((v) => v !== val) : [...values, val];
      props.setValue(newValues);
    }
  };

  const options = (props.options || []).filter(
    (opt) => !props.filterable || filter === '' || String(opt.label).toLowerCase().includes(filter.toLowerCase())
  );
  const listClasses = `options${props.displayStyle === 'chips' ? ' chips' : ''}`;

  return (
    <div className="host">
      {props.label && <strong className="label">{props.label}</strong>}
      {props.filterable && (
        <input
          type="text"
          placeholder="Filter options..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="filterInput"
        />
      )}
      <div className={listClasses}>
        {options.map((opt, i) => {
          const isSelected = values.includes(opt.value);
          if (props.displayStyle === 'chips') {
            return (
              <button
                key={i}
                onClick={() => onToggle(opt.value)}
                className={`chip${isSelected ? ' selected' : ''}`}
                aria-pressed={isSelected}
              >
                {optionLabelText(opt.label)}
              </button>
            );
          }
          return (
            <label key={i} className="optionLabel">
              <input
                type={isMutuallyExclusive ? 'radio' : 'checkbox'}
                checked={isSelected}
                onChange={() => onToggle(opt.value)}
                name={isMutuallyExclusive ? `choice-${context.componentModel.id}` : undefined}
              />
              <span className="optionText">{optionLabelText(opt.label)}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
});
