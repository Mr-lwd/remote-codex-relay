import { useState } from 'react';

// Setters retain their originating scope across awaits and navigation.
export function useSessionState(scope, initial) {
  const [values, setValues] = useState({});
  const value = values[scope] ?? initial;
  function setValue(update) {
    setValues((all) => {
      const current = all[scope] ?? initial;
      const next = typeof update === 'function' ? update(current) : update;
      return Object.is(current, next) ? all : { ...all, [scope]: next };
    });
  }
  return [value, setValue];
}
