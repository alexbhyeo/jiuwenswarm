// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

// Workaround for a confirmed bug in the published @a2ui/react package (still
// present as of 0.10.2): Button's own CSS-module class map compiles to an
// empty object (`var Button_default = {}`), so its className ends up as the
// literal string "undefined" and every button renders with zero styling.
// The real, unhashed selectors (.button/.primary/.borderless) already exist
// in the same package's shipped index.css (loaded as a JS side effect), so
// this override only needs to apply the literal class names correctly -
// no new styling was designed here, just Google's own CSS wired up right.
import { ButtonApi } from '@a2ui/web_core/v0_9/basic_catalog';
import { createComponentImplementation } from '@a2ui/react/v0_9';

export const ButtonWithStyles = createComponentImplementation(ButtonApi, ({ props, buildChild }) => {
  const classes = ['button'];
  if (props.variant === 'primary') classes.push('primary');
  else if (props.variant === 'borderless') classes.push('borderless');
  return (
    <button
      className={classes.join(' ')}
      onClick={props.action}
      disabled={props.isValid === false}
    >
      {props.child ? buildChild(props.child) : null}
    </button>
  );
});
