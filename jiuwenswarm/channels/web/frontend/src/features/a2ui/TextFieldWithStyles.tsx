// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

// Workaround for two confirmed bugs in the published @a2ui/react package
// (still present as of 0.10.2): (1) TextField's own CSS-module class map
// compiles to an empty object (`var TextField_default = {}`), so its
// className ends up as the literal string "undefined" and the field
// renders with zero styling; (2) even the real, unhashed selectors
// (.host/.label/.input/.invalid/.error) in the package's shipped
// v0_9/index.css are never imported by the package's own JS and aren't in
// its exports map, so that CSS can't reach our bundle either - see
// a2ui.css for a verbatim copy of those rules. This override just applies
// the literal class names correctly - no new styling was designed here,
// just Google's own CSS wired up right.
import { useId } from 'react';
import { TextFieldApi } from '@a2ui/web_core/v0_9/basic_catalog';
import { createComponentImplementation } from '@a2ui/react/v0_9';

export const TextFieldWithStyles = createComponentImplementation(TextFieldApi, ({ props }) => {
  const onChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    props.setValue(e.target.value);
  };
  const isLong = props.variant === 'longText';
  const type = props.variant === 'number' ? 'number' : props.variant === 'obscured' ? 'password' : 'text';
  const uniqueId = useId();
  const hasError = Boolean(props.validationErrors && props.validationErrors.length > 0);
  const inputClasses = `input${hasError ? ' invalid' : ''}`;

  return (
    <div className="host">
      {props.label && (
        <label htmlFor={uniqueId} className="label">
          {props.label}
        </label>
      )}
      {isLong ? (
        <textarea id={uniqueId} className={inputClasses} value={props.value || ''} onChange={onChange} />
      ) : (
        <input id={uniqueId} type={type} className={inputClasses} value={props.value || ''} onChange={onChange} />
      )}
      {hasError && <span className="error">{props.validationErrors![0]}</span>}
    </div>
  );
});
