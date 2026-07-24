// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

// Workaround for two confirmed bugs in the published @a2ui/react package
// (still present as of 0.10.2): (1) Button's own CSS-module class map
// compiles to an empty object (`var Button_default = {}`), so its className
// ends up as the literal string "undefined" and every button renders with
// zero styling; (2) even the real, unhashed selectors (.button/.primary/
// .borderless) in the package's shipped v0_9/index.css are never imported
// by the package's own JS and aren't in its exports map, so that CSS can't
// reach our bundle either - see a2ui.css for a verbatim copy of those
// rules. This override just applies the literal class names correctly - no
// new styling was designed here, just Google's own CSS wired up right.
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
