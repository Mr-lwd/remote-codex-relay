import React, { useState, useRef } from 'react';
import { Button, Popover } from '@radix-ui/themes';
import { CaretRight, CheckCircle } from '@phosphor-icons/react';
export const DEFAULT_CONFIG = {
  models: [
    { id: 'gpt-5.6-sol', name: 'GPT-5.6-Sol' },
    { id: 'gpt-6-astra', name: 'GPT-6-Astra' },
  ],
  efforts: ['low', 'medium', 'high', 'xhigh', 'max'],
  defaultModel: 'gpt-5.6-sol',
  defaultEffort: 'medium',
};
export function normalizePreference(value, config = DEFAULT_CONFIG) {
  return {
    id: config.models.some((m) => m.id === value?.id) ? value.id : config.defaultModel,
    effort: config.efforts.includes(value?.effort) ? value.effort : config.defaultEffort,
  };
}
export function validPreference(value, config = DEFAULT_CONFIG) {
  return config.models.some((m) => m.id === value.id) && config.efforts.includes(value.effort);
}
export function ModelPicker({
  label,
  preference,
  onChange,
  disabled = false,
  config = DEFAULT_CONFIG,
}) {
  const MODELS = config.models,
    EFFORTS = config.efforts;
  const [open, setOpen] = useState(false),
    [draft, setDraft] = useState(null);
  const modelMenu = useRef(null),
    effortMenu = useRef(null);
  const name = MODELS.find((m) => m.id === preference.id)?.name;
  function openModel(id, focus = false) {
    setDraft(id);
    if (focus) requestAnimationFrame(() => effortMenu.current?.querySelector('button')?.focus());
  }
  function navigate(e, container) {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    const items = [...container.current.querySelectorAll('button')];
    const i = items.indexOf(document.activeElement);
    items[
      e.key === 'Home'
        ? 0
        : e.key === 'End'
          ? items.length - 1
          : (i + (e.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length
    ]?.focus();
  }
  return (
    <div className="model-picker">
      <Popover.Root
        open={open}
        onOpenChange={(value) => {
          setOpen(value);
          setDraft(null);
        }}
      >
        <Popover.Trigger>
          <Button
            type="button"
            variant="surface"
            className="model-trigger"
            aria-label={label}
            title={`${name} · ${preference.effort}`}
            disabled={disabled}
          >
            <span>{name}</span>
            <span className="selected-effort">{preference.effort}</span>
            <CaretRight size={13} />
          </Button>
        </Popover.Trigger>
        <Popover.Content
          side="top"
          align="end"
          sideOffset={8}
          collisionPadding={10}
          className="model-menu"
          aria-label={label + '与强度'}
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            modelMenu.current?.querySelector(`[data-model="${preference.id}"]`)?.focus();
          }}
          onCloseAutoFocus={(e) => {
            // The closing animation may finish after typing has begun elsewhere.
            // Preserve the user's new focus; restore the trigger only from body.
            if (document.activeElement && document.activeElement !== document.body)
              e.preventDefault();
          }}
        >
          <div className="model-menu-columns">
            <div className="model-column">
              <p>模型</p>
              <div
                role="menu"
                aria-label="选择模型"
                ref={modelMenu}
                onKeyDown={(e) => navigate(e, modelMenu)}
              >
                {MODELS.map((model) => (
                  <button
                    type="button"
                    role="menuitem"
                    aria-haspopup="menu"
                    aria-expanded={draft === model.id}
                    data-model={model.id}
                    data-current={preference.id === model.id}
                    key={model.id}
                    onClick={() => openModel(model.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'ArrowRight') {
                        e.preventDefault();
                        openModel(model.id, true);
                      }
                    }}
                  >
                    <span>{model.name}</span>
                    <CaretRight size={14} />
                  </button>
                ))}
              </div>
            </div>
            <div className="effort-column">
              {draft ? (
                <>
                  <p>推理强度</p>
                  <div
                    role="menu"
                    aria-label={MODELS.find((m) => m.id === draft).name + ' 推理强度'}
                    ref={effortMenu}
                    onKeyDown={(e) => {
                      if (e.key === 'ArrowLeft') {
                        e.preventDefault();
                        modelMenu.current?.querySelector(`[data-model="${draft}"]`)?.focus();
                        setDraft(null);
                      } else navigate(e, effortMenu);
                    }}
                  >
                    {EFFORTS.map((effort) => (
                      <button
                        type="button"
                        role="menuitemradio"
                        aria-checked={preference.id === draft && preference.effort === effort}
                        key={effort}
                        onClick={() => {
                          onChange({ id: draft, effort });
                          setOpen(false);
                          setDraft(null);
                        }}
                      >
                        <span>{effort}</span>
                        {preference.id === draft && preference.effort === effort && (
                          <CheckCircle size={14} />
                        )}
                      </button>
                    ))}
                  </div>
                </>
              ) : (
                <p className="effort-menu-hint">
                  选择模型后
                  <br />
                  在此选择强度
                </p>
              )}
            </div>
          </div>
        </Popover.Content>
      </Popover.Root>
    </div>
  );
}
