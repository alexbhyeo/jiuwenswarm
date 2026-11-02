import { useEffect, useId, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { X } from 'lucide-react';

import { Button, Select } from '../../../components/ui';
import type { DirectorAsset } from '../types';
import type { EffectDef } from '../directorApi';
import { useDirectorStore } from '../directorStore';
import { Dialog } from '../../../components/ui/Dialog/Dialog';
import './EffectsPanel.css';

/**
 * 剪辑"特效"面板：对一个已就绪素材（资产库卡片或时间线片段）应用一个 v1
 * 精选特效。应用永远产出一条新的派生素材（params.source_asset_id/effect_id/
 * effect_params 记溯源），不改动原素材的 file_path——与生成/合成同构，见
 * directorStore.applyEffect 和后端 handle_director_clip_effect_apply。
 *
 * 分组标题（调色/模糊/…）故意不在这里展示：目录的 group 字段只是后端内部
 * 分类，没有对应的前端 i18n key，直接显示会在英文界面漏出中文——v1 按
 * asset 类型过滤后平铺展示即可，效果数量不多，不需要分组导航。
 */
export function EffectsPanel({
  open,
  projectId,
  asset,
  trimRange,
  onClose,
}: {
  open: boolean;
  projectId: string;
  asset: DirectorAsset | null;
  /** 仅时间线片段入口传入：该片段在源文件内的裁剪窗口（秒）——应用时只处理
   *  这段范围，不处理整份源文件。素材库卡片入口不传（undefined/null），
   *  按整份素材处理。 */
  trimRange?: { trimIn: number; trimOut: number } | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const titleId = useId();
  const effectCatalog = useDirectorStore((s) => s.effectCatalog);
  const effectCatalogLoading = useDirectorStore((s) => s.effectCatalogLoading);
  const loadEffectCatalog = useDirectorStore((s) => s.loadEffectCatalog);
  const applyingEffect = useDirectorStore((s) => s.applyingEffect);
  const effectError = useDirectorStore((s) => s.effectError);
  const applyEffect = useDirectorStore((s) => s.applyEffect);

  const [selectedEffectId, setSelectedEffectId] = useState<string | null>(null);
  const [paramValues, setParamValues] = useState<Record<string, unknown>>({});

  useEffect(() => {
    if (open) void loadEffectCatalog();
  }, [open, loadEffectCatalog]);

  useEffect(() => {
    if (!open) {
      setSelectedEffectId(null);
      setParamValues({});
    }
  }, [open]);

  const applicableEffects = useMemo<EffectDef[]>(() => {
    if (!asset) return [];
    return effectCatalog.filter((e) => e.assetTypes.includes(asset.type as 'image' | 'video' | 'audio'));
  }, [effectCatalog, asset]);

  const selectedEffect = applicableEffects.find((e) => e.effectId === selectedEffectId) ?? null;

  const selectEffect = (effect: EffectDef) => {
    setSelectedEffectId(effect.effectId);
    const defaults: Record<string, unknown> = {};
    for (const p of effect.params) defaults[p.key] = p.default;
    setParamValues(defaults);
  };

  const apply = async () => {
    if (!asset || !selectedEffect) return;
    await applyEffect(projectId, asset.asset_id, selectedEffect.effectId, paramValues, trimRange);
    if (!useDirectorStore.getState().effectError) onClose();
  };

  if (!asset) return null;

  return (
    <Dialog open={open} titleId={titleId} className="effects-panel-dialog" closeDisabled={applyingEffect} onCancel={onClose}>
      <div className="effects-panel" data-testid="director-effects-panel">
        <header className="effects-panel__header">
          <h2 id={titleId}>{t('director.effects.panelTitle')}</h2>
          <button
            type="button"
            className="effects-panel__close"
            aria-label={t('common.close')}
            title={t('common.close')}
            disabled={applyingEffect}
            onClick={onClose}
            data-testid="director-effects-panel-close-btn"
          >
            <X aria-hidden />
          </button>
        </header>
        <p className="effects-panel__source" data-testid="director-effects-panel-source">
          {trimRange
            ? t('director.effects.sourceAssetRange', {
                name: asset.name || asset.prompt || asset.asset_id,
                start: trimRange.trimIn.toFixed(1),
                end: trimRange.trimOut.toFixed(1),
              })
            : t('director.effects.sourceAsset', { name: asset.name || asset.prompt || asset.asset_id })}
        </p>
        <div className="effects-panel__body">
          {effectCatalogLoading ? null : applicableEffects.length === 0 ? (
            <p className="effects-panel__empty" data-testid="director-effects-panel-empty">
              {t('director.effects.noEffects')}
            </p>
          ) : (
            <div className="effects-panel__list" data-testid="director-effects-panel-list">
              {applicableEffects.map((effect) => (
                <button
                  key={effect.effectId}
                  type="button"
                  className="effects-panel__effect-btn"
                  aria-pressed={effect.effectId === selectedEffectId}
                  onClick={() => selectEffect(effect)}
                  data-testid="director-effects-panel-effect-option"
                  data-variant={effect.effectId}
                >
                  {t(`director.effects.catalog.${effect.effectId}.label`, { defaultValue: effect.label })}
                </button>
              ))}
            </div>
          )}

          {selectedEffect ? (
            <div className="effects-panel__params" data-testid="director-effects-panel-params">
              {selectedEffect.params.map((p) => {
                const paramLabel = t(`director.effects.catalog.${selectedEffect.effectId}.params.${p.key}`, {
                  defaultValue: p.label,
                });
                const fieldId = `${titleId}-${p.key}`;
                if (p.kind === 'select') {
                  const options = p.options.map((o) => ({
                    value: o.value,
                    label: t(`director.effects.catalog.${selectedEffect.effectId}.options.${o.value}`, {
                      defaultValue: o.label,
                    }),
                  }));
                  return (
                    <div className="effects-panel__field" key={p.key}>
                      <label htmlFor={fieldId}>{paramLabel}</label>
                      <Select
                        id={fieldId}
                        value={paramValues[p.key] as string}
                        options={options}
                        onChange={(value) => setParamValues((s) => ({ ...s, [p.key]: value }))}
                        data-testid="director-effects-panel-param-select"
                      />
                    </div>
                  );
                }
                const numericValue = typeof paramValues[p.key] === 'number' ? (paramValues[p.key] as number) : 0;
                return (
                  <div className="effects-panel__field" key={p.key}>
                    <label htmlFor={fieldId}>
                      {paramLabel}
                      <span className="effects-panel__field-value">{numericValue}</span>
                    </label>
                    <input
                      id={fieldId}
                      type="range"
                      min={p.min ?? 0}
                      max={p.max ?? 100}
                      value={numericValue}
                      onChange={(e) => setParamValues((s) => ({ ...s, [p.key]: Number(e.target.value) }))}
                      data-testid="director-effects-panel-param-range"
                    />
                  </div>
                );
              })}
            </div>
          ) : null}

          {effectError ? (
            <div className="effects-panel__error" role="alert" data-testid="director-effects-panel-error">
              {t('director.effects.applyFailed', { message: effectError })}
            </div>
          ) : null}
        </div>
        <footer className="effects-panel__footer">
          <Button size="sm" disabled={applyingEffect} onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            size="sm"
            loading={applyingEffect}
            disabled={!selectedEffect || applyingEffect}
            onClick={() => void apply()}
            data-testid="director-effects-panel-apply-btn"
          >
            {applyingEffect ? t('director.effects.applying') : t('director.effects.apply')}
          </Button>
        </footer>
      </div>
    </Dialog>
  );
}
