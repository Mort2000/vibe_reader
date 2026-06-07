import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  ChevronDown,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Lock,
  Plus,
  RefreshCcw,
  RotateCcw,
  Save,
  Search,
  TestTube2,
  Trash2,
  Undo2,
  X,
} from 'lucide-react';

import { StatusPill } from '../../components/StatusPill';
import { ApiError, api, describeError } from '../../lib/api';
import { queryKeys } from '../../lib/apiQueries';
import type {
  ConfigDocument,
  ConfigFieldMetadata,
  ConfigFile,
  ConfigGroupValue,
  EffectiveModelSummary,
  LoadStatus,
  ModelConfigSummary,
  ModelPingResult,
  ModelRefs,
} from '../../types';

const SETTINGS_GROUPS = [
  'reader',
  'window_l1',
  'context',
  'context_l2',
  'context_l3',
  'ephemeral_comments',
  'ephemeral_chat',
  'token_estimation',
  'observability',
] as const;

const COMMON_RESETS = [
  {
    id: 'llm',
    label: 'LLM 相关',
    description: '清空模型目录、默认引用和当前切换。',
  },
  {
    id: 'reader_window',
    label: '阅读与窗口',
    description: '恢复 reader 与 window_l1。',
  },
  {
    id: 'context_budget',
    label: '上下文预算',
    description: '恢复 context、context_l2 与 context_l3。',
  },
  {
    id: 'observability_common',
    label: '可观测常用',
    description: '恢复日志级别、sink 和 OTEL 常用项。',
  },
] as const;

const OBSERVABILITY_COMMON_PATHS = [
  'observability.log_level',
  'observability.log_sinks',
  'observability.otel.enabled',
  'observability.otel.endpoint',
  'observability.otel.export_traces',
  'observability.otel.export_metrics',
  'observability.otel.export_logs',
];

const EMPTY_REFS: ModelRefs = {
  global_model_id: '',
  chat_model_id: '',
  comment_model_id: '',
};

const THINK_EFFORT_OPTIONS = ['', 'minimal', 'low', 'medium', 'high'];

interface ActionState {
  status: LoadStatus;
  label: string;
  detail?: string;
}

interface ModelForm {
  id: string;
  provider: string;
  url: string;
  model_name: string;
  api_key: string;
  think_effort: string;
  apiKeyTouched: boolean;
  showApiKey: boolean;
}

interface ModelEditor {
  mode: 'new' | 'edit';
  originalId?: string;
  form: ModelForm;
  errors: Record<string, string>;
}

interface PingState {
  status: LoadStatus;
  label: string;
  detail?: string;
  result?: ModelPingResult;
}

export function ConfigurationPage({
  onDirtyChange,
}: {
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [doc, setDoc] = useState<ConfigDocument | null>(null);
  const [draft, setDraft] = useState<ConfigFile | null>(null);
  const [baseline, setBaseline] = useState('');
  const [loadState, setLoadState] = useState<ActionState>({
    status: 'loading',
    label: '加载配置',
  });
  const [actionState, setActionState] = useState<ActionState>({
    status: 'idle',
    label: '等待编辑',
  });
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [search, setSearch] = useState('');
  const [editor, setEditor] = useState<ModelEditor | null>(null);
  const [pingState, setPingState] = useState<Record<string, PingState>>({});
  const [saving, setSaving] = useState(false);

  const applyDocument = useCallback((nextDoc: ConfigDocument) => {
    const nextDraft = cloneConfig(nextDoc.config);
    setDoc(nextDoc);
    setDraft(nextDraft);
    setBaseline(configFingerprint(nextDraft));
    setFieldErrors({});
    setEditor(null);
  }, []);

  const loadConfig = useCallback(async () => {
    setLoadState({ status: 'loading', label: '加载配置' });
    try {
      const nextDoc = await api.config();
      applyDocument(nextDoc);
      setLoadState({ status: 'success', label: '配置已加载' });
      setActionState({ status: 'idle', label: '等待编辑' });
    } catch (error) {
      const described = describeError(error);
      setLoadState({
        status: 'error',
        label: '配置加载失败',
        detail: described.detail,
      });
    }
  }, [applyDocument]);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  const dirty = useMemo(
    () => (draft ? configFingerprint(draft) !== baseline : false),
    [baseline, draft],
  );

  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  useEffect(() => {
    if (!dirty) return undefined;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [dirty]);

  const invalidateSummaries = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.runtime() }),
      queryClient.invalidateQueries({ queryKey: queryKeys.settings() }),
    ]);
  }, [queryClient]);

  const metadata = doc?.metadata;
  const modelMetadata = metadata?.groups.models;
  const modelOptions = draft?.models ?? [];
  const maskedSecret = modelMetadata?.secret_policy?.masked_value ?? '********';

  const updateDraft = useCallback((updater: (current: ConfigFile) => ConfigFile) => {
    setDraft((current) => (current ? updater(current) : current));
  }, []);

  const handleFieldChange = useCallback(
    (path: string, value: unknown) => {
      updateDraft((current) => setConfigPath(current, path, value));
      setFieldErrors((current) => {
        const next = { ...current };
        delete next[path];
        return next;
      });
    },
    [updateDraft],
  );

  const resetField = useCallback(
    (field: ConfigFieldMetadata) => {
      if (!window.confirm(`确认将“${field.label}”恢复默认值？`)) return;
      handleFieldChange(field.path, cloneValue(field.default));
      setActionState({ status: 'success', label: `${field.label} 已恢复默认` });
    },
    [handleFieldChange],
  );

  const resetGroup = useCallback(
    (groupName: string) => {
      const group = metadata?.groups[groupName];
      if (!group) return;
      if (!window.confirm(`确认将“${group.label}”分组恢复默认值？`)) return;
      updateDraft((current) => {
        let next = current;
        Object.values(group.fields).forEach((field) => {
          next = setConfigPath(next, field.path, cloneValue(field.default));
        });
        return next;
      });
      setActionState({ status: 'success', label: `${group.label} 已恢复默认` });
    },
    [metadata, updateDraft],
  );

  const resetPreset = useCallback(
    (preset: string) => {
      const reset = COMMON_RESETS.find((item) => item.id === preset);
      if (!reset || !metadata) return;
      if (!window.confirm(`确认执行“${reset.label}”快捷重置？`)) return;
      updateDraft((current) => {
        if (preset === 'llm') {
          return { ...current, models: [], defaults: { ...EMPTY_REFS }, active: { ...EMPTY_REFS } };
        }
        if (preset === 'reader_window') {
          return resetGroupsToDefaults(current, metadata, ['reader', 'window_l1']);
        }
        if (preset === 'context_budget') {
          return resetGroupsToDefaults(current, metadata, [
            'context',
            'context_l2',
            'context_l3',
          ]);
        }
        let next = current;
        OBSERVABILITY_COMMON_PATHS.forEach((path) => {
          const field = metadata.groups.observability?.fields[path];
          if (field) next = setConfigPath(next, path, cloneValue(field.default));
        });
        return next;
      });
      if (preset === 'llm') setEditor(null);
      setActionState({ status: 'success', label: `${reset.label} 已重置` });
    },
    [metadata, updateDraft],
  );

  const validateAndSave = useCallback(async () => {
    if (!draft || !metadata) return;
    const errors = validateConfigDraft(draft, metadata);
    if (Object.keys(errors).length) {
      setFieldErrors(errors);
      setActionState({
        status: 'error',
        label: '保存前校验失败',
        detail: '请检查已标红的字段。',
      });
      return;
    }

    setSaving(true);
    setActionState({ status: 'loading', label: '保存配置' });
    try {
      const nextDoc = await api.saveConfig(draft);
      applyDocument(nextDoc);
      await invalidateSummaries();
      setActionState({ status: 'success', label: '配置已保存并应用' });
    } catch (error) {
      setFieldErrors(fieldErrorsFromApi(error));
      const described = describeError(error);
      setActionState({
        status: 'error',
        label: described.title || '保存失败',
        detail: described.detail,
      });
    } finally {
      setSaving(false);
    }
  }, [applyDocument, draft, invalidateSummaries, metadata]);

  const discardChanges = useCallback(() => {
    if (!doc) return;
    if (dirty && !window.confirm('确认放弃所有未保存修改？')) return;
    applyDocument(doc);
    setActionState({ status: 'idle', label: '已放弃未保存修改' });
  }, [applyDocument, dirty, doc]);

  const startNewModel = useCallback(() => {
    setEditor({
      mode: 'new',
      form: {
        id: '',
        provider: 'openai_compatible',
        url: '',
        model_name: '',
        api_key: '',
        think_effort: '',
        apiKeyTouched: true,
        showApiKey: false,
      },
      errors: {},
    });
  }, []);

  const startEditModel = useCallback((model: ModelConfigSummary) => {
    setEditor({
      mode: 'edit',
      originalId: model.id,
      form: {
        id: model.id,
        provider: model.provider,
        url: model.url,
        model_name: model.model_name,
        api_key: '',
        think_effort: model.think_effort,
        apiKeyTouched: false,
        showApiKey: false,
      },
      errors: {},
    });
  }, []);

  const updateModelForm = useCallback(
    (field: keyof ModelForm, value: string | boolean) => {
      setEditor((current) => {
        if (!current) return current;
        return {
          ...current,
          form: { ...current.form, [field]: value },
          errors: { ...current.errors, [field]: '' },
        };
      });
    },
    [],
  );

  const saveModelFromEditor = useCallback(() => {
    if (!editor || !draft) return;
    const errors = validateModelForm(editor, draft.models);
    if (Object.keys(errors).length) {
      setEditor((current) => (current ? { ...current, errors } : current));
      return;
    }

    const nextModel = modelFromEditor(editor, draft.models);
    updateDraft((current) => {
      const existingModels = current.models;
      const replacing = editor.mode === 'edit';
      const models = replacing
        ? existingModels.map((model) =>
            model.id === editor.originalId ? nextModel : model,
          )
        : [...existingModels, nextModel];
      const refs =
        existingModels.length === 0
          ? {
              defaults: {
                global_model_id: nextModel.id,
                chat_model_id: nextModel.id,
                comment_model_id: nextModel.id,
              },
              active: { ...EMPTY_REFS },
            }
          : { defaults: current.defaults, active: current.active };
      return { ...current, models, ...refs };
    });
    setEditor(null);
    setActionState({
      status: 'success',
      label: editor.mode === 'new' ? '模型已加入草稿' : '模型草稿已更新',
    });
  }, [draft, editor, updateDraft]);

  const deleteDraftModel = useCallback(
    (model: ModelConfigSummary) => {
      if (!draft) return;
      const refs = referencingModelPaths(draft, model.id);
      if (refs.length) {
        setActionState({
          status: 'error',
          label: '模型正在被引用',
          detail: `请先切换这些引用：${refs.join('、')}`,
        });
        return;
      }
      if (!window.confirm(`确认删除模型“${model.id}”？保存后才会持久化。`)) return;
      updateDraft((current) => ({
        ...current,
        models: current.models.filter((item) => item.id !== model.id),
      }));
      if (editor?.originalId === model.id) setEditor(null);
      setActionState({ status: 'success', label: '模型已从草稿移除' });
    },
    [draft, editor?.originalId, updateDraft],
  );

  const setDefaultRef = useCallback(
    (field: keyof ModelRefs, modelId: string) => {
      updateDraft((current) => ({
        ...current,
        defaults: { ...current.defaults, [field]: modelId },
      }));
      setFieldErrors((current) => {
        const next = { ...current };
        delete next[`defaults.${field}`];
        return next;
      });
    },
    [updateDraft],
  );

  const switchActive = useCallback(
    async (scope: 'global' | 'chat' | 'comment', modelId: string) => {
      if (dirty && !window.confirm('切换当前模型会放弃未保存草稿，是否继续？')) {
        return;
      }
      setActionState({ status: 'loading', label: '切换当前模型' });
      try {
        const nextDoc = await api.switchActiveModel(scope, modelId);
        applyDocument(nextDoc);
        await invalidateSummaries();
        setActionState({ status: 'success', label: '当前模型已切换' });
      } catch (error) {
        const described = describeError(error);
        setActionState({
          status: 'error',
          label: described.title || '切换失败',
          detail: described.detail,
        });
      }
    },
    [applyDocument, dirty, invalidateSummaries],
  );

  const pingModel = useCallback(
    async (key: string, body: Parameters<typeof api.pingModel>[0]) => {
      setPingState((current) => ({
        ...current,
        [key]: { status: 'loading', label: '测试连接' },
      }));
      try {
        const result = await api.pingModel(body);
        setPingState((current) => ({
          ...current,
          [key]: {
            status: 'success',
            label: '连接成功',
            detail: pingSummary(result),
            result,
          },
        }));
      } catch (error) {
        const described = describeError(error);
        setPingState((current) => ({
          ...current,
          [key]: {
            status: 'error',
            label: described.title || '测试失败',
            detail: described.detail,
          },
        }));
      }
    },
    [],
  );

  const pingDraftModel = useCallback(
    (model: ModelConfigSummary) => {
      if (!doc) return;
      void pingModel(`model:${model.id}`, pingBodyForModel(model, doc, maskedSecret));
    },
    [doc, maskedSecret, pingModel],
  );

  const pingEditorModel = useCallback(() => {
    if (!editor || !doc) return;
    const body = pingBodyForEditor(editor, doc);
    void pingModel('editor', body);
  }, [doc, editor, pingModel]);

  if (loadState.status === 'loading' && !doc) {
    return (
      <section className="config-page config-loading">
        <Loader2 size={22} className="spin" />
        <strong>正在加载配置...</strong>
      </section>
    );
  }

  if (!doc || !draft || !metadata) {
    return (
      <section className="config-page config-loading">
        <AlertCircle size={22} />
        <strong>{loadState.label}</strong>
        {loadState.detail && <p>{loadState.detail}</p>}
        <button className="soft-inline-button" onClick={() => void loadConfig()}>
          <RefreshCcw size={16} />
          重试
        </button>
      </section>
    );
  }

  return (
    <section className="config-page">
      <header className="config-header">
        <div>
          <span className="eyebrow">配置管理</span>
          <h1>后端 Settings 与模型目录</h1>
          <p>
            {doc.policy.in_flight_model_switch} {doc.policy.compaction_model}
          </p>
        </div>
        <div className="config-actions">
          <StatusPill status={dirty ? 'loading' : actionState.status}>
            {saving && <Loader2 size={14} className="spin" />}
            {dirty ? '有未保存修改' : actionState.label}
          </StatusPill>
          <button
            className="soft-inline-button"
            disabled={!dirty || saving}
            onClick={discardChanges}
          >
            <Undo2 size={16} />
            放弃
          </button>
          <button
            className="send-button"
            disabled={!dirty || saving}
            onClick={() => void validateAndSave()}
          >
            {saving ? <Loader2 size={16} className="spin" /> : <Save size={16} />}
            保存
          </button>
        </div>
      </header>

      {actionState.detail && (
        <div className={`config-message message-${actionState.status}`}>
          {actionState.status === 'error' ? <AlertCircle size={16} /> : <CheckCircle2 size={16} />}
          <span>{actionState.detail}</span>
        </div>
      )}

      <div className="config-layout">
        <aside className="config-nav" aria-label="配置分组导航">
          <label className="config-search">
            <Search size={16} />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索配置项"
            />
          </label>
          <nav>
            <a href="#config-models">模型</a>
            {SETTINGS_GROUPS.map((groupName) => (
              <a href={`#config-${groupName}`} key={groupName}>
                {metadata.groups[groupName]?.label ?? groupName}
              </a>
            ))}
          </nav>
          <div className="common-reset-panel">
            <strong>常用重置</strong>
            {COMMON_RESETS.map((reset) => (
              <button
                className="soft-inline-button"
                key={reset.id}
                title={reset.description}
                onClick={() => resetPreset(reset.id)}
              >
                <RotateCcw size={15} />
                {reset.label}
              </button>
            ))}
          </div>
        </aside>

        <div className="config-main">
          <ModelManagement
            doc={doc}
            draft={draft}
            editor={editor}
            fieldErrors={fieldErrors}
            modelMetadata={modelMetadata}
            modelOptions={modelOptions}
            pingState={pingState}
            onCreate={startNewModel}
            onEdit={startEditModel}
            onDelete={deleteDraftModel}
            onDefaultChange={setDefaultRef}
            onSwitchActive={switchActive}
            onPingModel={pingDraftModel}
            onPingEditor={pingEditorModel}
            onUpdateEditor={updateModelForm}
            onCancelEditor={() => setEditor(null)}
            onSaveEditor={saveModelFromEditor}
          />

          {SETTINGS_GROUPS.map((groupName) => (
            <SettingsGroupPanel
              key={groupName}
              groupName={groupName}
              metadata={metadata}
              draft={draft}
              search={search}
              fieldErrors={fieldErrors}
              onChange={handleFieldChange}
              onResetField={resetField}
              onResetGroup={resetGroup}
            />
          ))}
        </div>
      </div>
    </section>
  );
}

function ModelManagement({
  doc,
  draft,
  editor,
  fieldErrors,
  modelMetadata,
  modelOptions,
  pingState,
  onCreate,
  onEdit,
  onDelete,
  onDefaultChange,
  onSwitchActive,
  onPingModel,
  onPingEditor,
  onUpdateEditor,
  onCancelEditor,
  onSaveEditor,
}: {
  doc: ConfigDocument;
  draft: ConfigFile;
  editor: ModelEditor | null;
  fieldErrors: Record<string, string>;
  modelMetadata: ConfigDocument['metadata']['groups'][string] | undefined;
  modelOptions: ModelConfigSummary[];
  pingState: Record<string, PingState>;
  onCreate: () => void;
  onEdit: (model: ModelConfigSummary) => void;
  onDelete: (model: ModelConfigSummary) => void;
  onDefaultChange: (field: keyof ModelRefs, modelId: string) => void;
  onSwitchActive: (scope: 'global' | 'chat' | 'comment', modelId: string) => void;
  onPingModel: (model: ModelConfigSummary) => void;
  onPingEditor: () => void;
  onUpdateEditor: (field: keyof ModelForm, value: string | boolean) => void;
  onCancelEditor: () => void;
  onSaveEditor: () => void;
}) {
  const readOnlyEnv = modelMetadata?.read_only_env ?? [];
  const ignoredEnv = modelMetadata?.ignored_env ?? [];

  return (
    <section className="config-section model-section" id="config-models">
      <div className="config-section-heading">
        <div>
          <Bot size={18} />
          <div>
            <h2>模型</h2>
            <p>{modelMetadata?.description ?? '维护 LLM 连接配置。'}</p>
          </div>
        </div>
        <button className="send-button" onClick={onCreate}>
          <Plus size={16} />
          新建模型
        </button>
      </div>

      {readOnlyEnv.length > 0 && draft.models.length === 0 && (
        <div className="config-notice">
          <Lock size={16} />
          <span>
            检测到 LLM 环境变量：{readOnlyEnv.join('、')}。页面只读展示，不会自动持久化密钥。
          </span>
        </div>
      )}
      {ignoredEnv.length > 0 && (
        <div className="config-notice muted">
          <AlertCircle size={16} />
          <span>
            已有本地模型目录，忽略这些 LLM 环境变量：{ignoredEnv.join('、')}。
          </span>
        </div>
      )}

      <div className="model-table" role="table" aria-label="模型目录">
        <div className="model-table-row model-table-head" role="row">
          <span>模型 ID</span>
          <span>Provider / URL</span>
          <span>模型名称</span>
          <span>密钥</span>
          <span>操作</span>
        </div>
        {draft.models.length ? (
          draft.models.map((model) => (
            <div className="model-table-row" role="row" key={model.id}>
              <strong>{model.id}</strong>
              <span>
                {model.provider}
                <small title={model.url}>{model.url || '未配置 URL'}</small>
              </span>
              <span>
                {model.model_name}
                <small>{model.think_effort || '无 thinking effort'}</small>
              </span>
              <span>
                <KeyRound size={14} />
                {model.api_key_configured || model.api_key ? 'Key 已配置' : 'Key 未配置'}
              </span>
              <span className="model-actions">
                <button
                  className="icon-button"
                  title="测试连接"
                  onClick={() => onPingModel(model)}
                >
                  <TestTube2 size={16} />
                </button>
                <button
                  className="icon-button"
                  title="编辑模型"
                  onClick={() => onEdit(model)}
                >
                  <Bot size={16} />
                </button>
                <button
                  className="icon-button danger-button"
                  title="删除模型"
                  onClick={() => onDelete(model)}
                >
                  <Trash2 size={16} />
                </button>
              </span>
              {pingState[`model:${model.id}`] && (
                <PingResult state={pingState[`model:${model.id}`]} />
              )}
            </div>
          ))
        ) : (
          <div className="empty-config-list">
            <Bot size={24} />
            <strong>尚未创建模型</strong>
            <span>新建模型并保存后，Chat 与评论即可引用。</span>
          </div>
        )}
      </div>

      {editor && (
        <ModelEditorForm
          editor={editor}
          pingState={pingState.editor}
          onChange={onUpdateEditor}
          onPing={onPingEditor}
          onCancel={onCancelEditor}
          onSave={onSaveEditor}
        />
      )}

      <div className="model-ref-grid">
        <ModelRefSelect
          label="全局默认"
          value={draft.defaults.global_model_id}
          error={fieldErrors['defaults.global_model_id']}
          models={modelOptions}
          onChange={(value) => onDefaultChange('global_model_id', value)}
        />
        <ModelRefSelect
          label="Chat 默认"
          value={draft.defaults.chat_model_id}
          error={fieldErrors['defaults.chat_model_id']}
          models={modelOptions}
          onChange={(value) => onDefaultChange('chat_model_id', value)}
        />
        <ModelRefSelect
          label="评论默认"
          value={draft.defaults.comment_model_id}
          error={fieldErrors['defaults.comment_model_id']}
          models={modelOptions}
          hint="Compaction 与评论共用"
          onChange={(value) => onDefaultChange('comment_model_id', value)}
        />
      </div>

      <div className="effective-models">
        <EffectiveModelCard label="全局当前" summary={doc.effective.global} />
        <EffectiveModelCard label="Chat 当前" summary={doc.effective.chat} />
        <EffectiveModelCard label="评论当前" summary={doc.effective.comment} />
        <EffectiveModelCard label="压缩当前" summary={doc.effective.compaction} />
      </div>

      <div className="active-switch-grid">
        <ActiveModelSelect
          label="切换全局当前"
          value={doc.active.global_model_id}
          fallback="沿用全局默认"
          models={modelOptions}
          onChange={(value) => onSwitchActive('global', value)}
        />
        <ActiveModelSelect
          label="切换 Chat 当前"
          value={doc.active.chat_model_id}
          fallback="沿用 Chat 默认"
          models={modelOptions}
          onChange={(value) => onSwitchActive('chat', value)}
        />
        <ActiveModelSelect
          label="切换评论/压缩当前"
          value={doc.active.comment_model_id}
          fallback="沿用评论默认"
          models={modelOptions}
          onChange={(value) => onSwitchActive('comment', value)}
        />
      </div>
    </section>
  );
}

function ModelEditorForm({
  editor,
  pingState,
  onChange,
  onPing,
  onCancel,
  onSave,
}: {
  editor: ModelEditor;
  pingState?: PingState;
  onChange: (field: keyof ModelForm, value: string | boolean) => void;
  onPing: () => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const { form, errors } = editor;
  return (
    <div className="model-editor">
      <div className="config-section-heading compact-heading">
        <div>
          <Bot size={18} />
          <div>
            <h3>{editor.mode === 'new' ? '新建模型' : `编辑 ${editor.originalId}`}</h3>
            <p>API Key 留空且未进入修改状态时，保存不会覆盖已有密钥。</p>
          </div>
        </div>
        <button className="icon-button" title="关闭编辑" onClick={onCancel}>
          <X size={16} />
        </button>
      </div>
      <div className="model-editor-grid">
        <FieldShell label="模型 ID" error={errors.id}>
          <input
            value={form.id}
            disabled={editor.mode === 'edit'}
            onChange={(event) => onChange('id', event.target.value)}
            placeholder="default"
          />
        </FieldShell>
        <FieldShell label="Provider" error={errors.provider}>
          <input
            value={form.provider}
            onChange={(event) => onChange('provider', event.target.value)}
            placeholder="openai_compatible"
          />
        </FieldShell>
        <FieldShell label="API Base URL" error={errors.url}>
          <input
            value={form.url}
            onChange={(event) => onChange('url', event.target.value)}
            placeholder="https://api.example.com/v1"
          />
        </FieldShell>
        <FieldShell label="模型名称" error={errors.model_name}>
          <input
            value={form.model_name}
            onChange={(event) => onChange('model_name', event.target.value)}
            placeholder="model-name"
          />
        </FieldShell>
        <FieldShell label="思考力度" error={errors.think_effort}>
          <select
            value={form.think_effort}
            onChange={(event) => onChange('think_effort', event.target.value)}
          >
            {THINK_EFFORT_OPTIONS.map((option) => (
              <option value={option} key={option || 'empty'}>
                {option || '留空'}
              </option>
            ))}
          </select>
        </FieldShell>
        <FieldShell
          label="API Key"
          error={errors.api_key}
          hint={
            editor.mode === 'edit' && !form.apiKeyTouched
              ? '已配置但未修改则不覆盖'
              : undefined
          }
        >
          <div className="secret-input">
            <input
              type={form.showApiKey ? 'text' : 'password'}
              value={form.api_key}
              onChange={(event) => {
                onChange('api_key', event.target.value);
                onChange('apiKeyTouched', true);
              }}
              placeholder={
                editor.mode === 'edit' ? '留空保留已有密钥' : 'sk-...'
              }
            />
            <button
              className="icon-button"
              title={form.showApiKey ? '隐藏密钥' : '显示密钥'}
              onClick={() => onChange('showApiKey', !form.showApiKey)}
            >
              {form.showApiKey ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </FieldShell>
      </div>
      {pingState && <PingResult state={pingState} />}
      <div className="model-editor-actions">
        <button className="soft-inline-button" onClick={onPing}>
          {pingState?.status === 'loading' ? (
            <Loader2 size={16} className="spin" />
          ) : (
            <TestTube2 size={16} />
          )}
          测试连接
        </button>
        <button className="send-button" onClick={onSave}>
          <CheckCircle2 size={16} />
          写入草稿
        </button>
      </div>
    </div>
  );
}

function SettingsGroupPanel({
  groupName,
  metadata,
  draft,
  search,
  fieldErrors,
  onChange,
  onResetField,
  onResetGroup,
}: {
  groupName: string;
  metadata: ConfigDocument['metadata'];
  draft: ConfigFile;
  search: string;
  fieldErrors: Record<string, string>;
  onChange: (path: string, value: unknown) => void;
  onResetField: (field: ConfigFieldMetadata) => void;
  onResetGroup: (groupName: string) => void;
}) {
  const group = metadata.groups[groupName];
  if (!group) return null;

  const fields = Object.values(group.fields).filter((field) =>
    fieldMatches(field, search),
  );
  const defaultOpen = ['reader', 'window_l1', 'context'].includes(groupName);

  return (
    <details
      className="config-group"
      id={`config-${groupName}`}
      open={defaultOpen || Boolean(search)}
    >
      <summary>
        <span>
          <ChevronDown size={16} />
          <strong>{group.label}</strong>
          <small>{group.description}</small>
        </span>
        <button
          className="soft-inline-button"
          onClick={(event) => {
            event.preventDefault();
            onResetGroup(groupName);
          }}
        >
          <RotateCcw size={15} />
          重置分组
        </button>
      </summary>
      {fields.length ? (
        <div className="config-field-grid">
          {fields.map((field) => (
            <ConfigFieldRow
              key={field.path}
              field={field}
              value={valueForField(draft, field)}
              error={fieldErrors[field.path]}
              onChange={(value) => onChange(field.path, value)}
              onReset={() => onResetField(field)}
            />
          ))}
        </div>
      ) : (
        <div className="empty-config-list">
          <Search size={20} />
          <span>当前搜索没有匹配配置项。</span>
        </div>
      )}
    </details>
  );
}

function ConfigFieldRow({
  field,
  value,
  error,
  onChange,
  onReset,
}: {
  field: ConfigFieldMetadata;
  value: unknown;
  error?: string;
  onChange: (value: unknown) => void;
  onReset: () => void;
}) {
  const locked = Boolean(field.read_only || field.env_override);
  const displayValue = field.env_override ? field.env_override.effective_value : value;

  return (
    <div className={`config-field ${locked ? 'is-locked' : ''}`}>
      <div className="config-field-top">
        <div>
          <strong>{field.label}</strong>
          <code>{field.path}</code>
        </div>
        <button
          className="icon-button"
          title="恢复默认值"
          disabled={locked}
          onClick={onReset}
        >
          <RotateCcw size={15} />
        </button>
      </div>
      <p>{field.description}</p>
      {renderFieldControl(field, displayValue, locked, onChange)}
      <div className="config-field-meta">
        <span>默认：{formatConfigValue(field.default)}</span>
        <span>{typeLabel(field)}</span>
      </div>
      {field.env_override && (
        <div className="env-lock">
          <Lock size={14} />
          <span>
            被 {field.env_override.env_var} 覆盖，当前生效值：
            {formatConfigValue(field.env_override.effective_value)}
          </span>
        </div>
      )}
      {error && <small className="field-error">{error}</small>}
    </div>
  );
}

function FieldShell({
  label,
  error,
  hint,
  children,
}: {
  label: string;
  error?: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="field-shell">
      <span>
        <strong>{label}</strong>
        {hint && <small>{hint}</small>}
      </span>
      {children}
      {error && <small className="field-error">{error}</small>}
    </label>
  );
}

function ModelRefSelect({
  label,
  value,
  models,
  error,
  hint,
  onChange,
}: {
  label: string;
  value: string;
  models: ModelConfigSummary[];
  error?: string;
  hint?: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="model-ref-select">
      <span>
        <strong>{label}</strong>
        {hint && <small>{hint}</small>}
      </span>
      <select
        value={value}
        disabled={!models.length}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">未指定</option>
        {models.map((model) => (
          <option value={model.id} key={model.id}>
            {model.id} · {model.model_name}
          </option>
        ))}
      </select>
      {error && <small className="field-error">{error}</small>}
    </label>
  );
}

function ActiveModelSelect({
  label,
  value,
  fallback,
  models,
  onChange,
}: {
  label: string;
  value: string;
  fallback: string;
  models: ModelConfigSummary[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="model-ref-select">
      <span>
        <strong>{label}</strong>
        <small>立即影响新请求</small>
      </span>
      <select
        value={value}
        disabled={!models.length}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{fallback}</option>
        {models.map((model) => (
          <option value={model.id} key={model.id}>
            {model.id} · {model.model_name}
          </option>
        ))}
      </select>
    </label>
  );
}

function EffectiveModelCard({
  label,
  summary,
}: {
  label: string;
  summary: EffectiveModelSummary;
}) {
  return (
    <div className="effective-model-card">
      <small>{label}</small>
      <strong title={summary.model_name}>{summary.model_name || '未配置'}</strong>
      <span>
        {summary.model_id || '无模型 ID'} · {summary.provider || 'unknown'}
      </span>
      <StatusPill status={summary.api_key_configured ? 'success' : 'error'}>
        {summary.api_key_configured ? 'Key 已配置' : 'Key 未配置'}
      </StatusPill>
    </div>
  );
}

function PingResult({ state }: { state: PingState }) {
  return (
    <div className={`ping-result ping-${state.status}`}>
      {state.status === 'loading' && <Loader2 size={15} className="spin" />}
      {state.status === 'success' && <CheckCircle2 size={15} />}
      {state.status === 'error' && <AlertCircle size={15} />}
      <span>{state.label}</span>
      {state.detail && <small>{state.detail}</small>}
    </div>
  );
}

function renderFieldControl(
  field: ConfigFieldMetadata,
  value: unknown,
  disabled: boolean,
  onChange: (value: unknown) => void,
) {
  const values = Array.isArray(field.constraints.values)
    ? field.constraints.values.map(String)
    : null;
  if (field.type === 'boolean') {
    return (
      <label className="config-toggle">
        <input
          type="checkbox"
          checked={Boolean(value)}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span>{Boolean(value) ? '开启' : '关闭'}</span>
      </label>
    );
  }
  if (field.type === 'enum' && values) {
    return (
      <select
        value={String(value ?? '')}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {values.map((option) => (
          <option value={option} key={option || 'empty'}>
            {option || '留空'}
          </option>
        ))}
      </select>
    );
  }
  if (field.type === 'integer' || field.type === 'float') {
    return (
      <input
        type="number"
        value={String(value ?? '')}
        min={numberConstraint(field, 'min') ?? undefined}
        max={numberConstraint(field, 'max') ?? undefined}
        step={field.type === 'integer' ? 1 : 0.01}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }
  return (
    <input
      value={stringInputValue(value)}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

function cloneConfig(config: ConfigFile): ConfigFile {
  return JSON.parse(JSON.stringify(config)) as ConfigFile;
}

function cloneValue(value: ConfigGroupValue): ConfigGroupValue {
  if (value === undefined) return value;
  return JSON.parse(JSON.stringify(value)) as ConfigGroupValue;
}

function configFingerprint(config: ConfigFile): string {
  return JSON.stringify(config);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function getConfigPath(config: ConfigFile, path: string): unknown {
  const [groupName, ...parts] = path.split('.');
  let current: unknown = config.groups[groupName];
  for (const part of parts) {
    if (!isRecord(current)) return undefined;
    current = current[part];
  }
  return current;
}

function setConfigPath(config: ConfigFile, path: string, value: unknown): ConfigFile {
  const [groupName, ...parts] = path.split('.');
  const group = isRecord(config.groups[groupName]) ? config.groups[groupName] : {};
  return {
    ...config,
    groups: {
      ...config.groups,
      [groupName]: setNestedValue(group, parts, value) as ConfigFile['groups'][string],
    },
  };
}

function setNestedValue(
  source: Record<string, unknown>,
  parts: string[],
  value: unknown,
): Record<string, unknown> {
  if (!parts.length) return source;
  const [head, ...tail] = parts;
  if (!tail.length) return { ...source, [head]: value };
  const child = isRecord(source[head]) ? source[head] : {};
  return { ...source, [head]: setNestedValue(child, tail, value) };
}

function resetGroupsToDefaults(
  config: ConfigFile,
  metadata: ConfigDocument['metadata'],
  groupNames: string[],
): ConfigFile {
  let next = config;
  groupNames.forEach((groupName) => {
    Object.values(metadata.groups[groupName]?.fields ?? {}).forEach((field) => {
      next = setConfigPath(next, field.path, cloneValue(field.default));
    });
  });
  return next;
}

function valueForField(config: ConfigFile, field: ConfigFieldMetadata): unknown {
  return getConfigPath(config, field.path);
}

function fieldMatches(field: ConfigFieldMetadata, search: string): boolean {
  const normalized = search.trim().toLowerCase();
  if (!normalized) return true;
  return `${field.label} ${field.path} ${field.description}`
    .toLowerCase()
    .includes(normalized);
}

function stringInputValue(value: unknown): string {
  if (Array.isArray(value)) return value.join(', ');
  if (value === null || value === undefined) return '';
  return String(value);
}

function formatConfigValue(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.join(', ') : '空列表';
  if (typeof value === 'boolean') return value ? '开启' : '关闭';
  if (value === '' || value === null || value === undefined) return '留空';
  if (isRecord(value)) return JSON.stringify(value);
  return String(value);
}

function numberConstraint(field: ConfigFieldMetadata, key: 'min' | 'max'): number | null {
  const value = field.constraints[key];
  return typeof value === 'number' ? value : null;
}

function typeLabel(field: ConfigFieldMetadata): string {
  const parts = [field.type || 'string'];
  const min = numberConstraint(field, 'min');
  const max = numberConstraint(field, 'max');
  if (min !== null) parts.push(`min ${min}`);
  if (max !== null) parts.push(`max ${max}`);
  if (Array.isArray(field.constraints.values)) {
    parts.push(`可选：${field.constraints.values.join(' / ') || '留空'}`);
  }
  return parts.join(' · ');
}

function validateConfigDraft(
  draft: ConfigFile,
  metadata: ConfigDocument['metadata'],
): Record<string, string> {
  const errors: Record<string, string> = {};
  const seen = new Set<string>();
  draft.models.forEach((model, idx) => {
    const basePath = `models[${idx}]`;
    if (!model.id.trim()) errors[`${basePath}.id`] = '模型 ID 不能为空';
    if (seen.has(model.id)) errors[`${basePath}.id`] = '模型 ID 必须唯一';
    seen.add(model.id);
    if (!model.model_name.trim()) {
      errors[`${basePath}.model_name`] = '模型名称不能为空';
    }
    if (!THINK_EFFORT_OPTIONS.includes(model.think_effort)) {
      errors[`${basePath}.think_effort`] = '思考力度取值无效';
    }
  });

  const catalog = new Set(draft.models.map((model) => model.id));
  (['global_model_id', 'chat_model_id', 'comment_model_id'] as const).forEach(
    (field) => {
      const value = draft.defaults[field];
      if (value && !catalog.has(value)) {
        errors[`defaults.${field}`] = '引用的模型不存在';
      }
    },
  );

  SETTINGS_GROUPS.forEach((groupName) => {
    Object.values(metadata.groups[groupName]?.fields ?? {}).forEach((field) => {
      if (field.read_only) return;
      const error = validateFieldValue(field, getConfigPath(draft, field.path));
      if (error) errors[field.path] = error;
    });
  });
  return errors;
}

function validateFieldValue(field: ConfigFieldMetadata, value: unknown): string | null {
  const required = Boolean(field.constraints.required);
  if (required && stringInputValue(value).trim() === '') return '该字段必填';
  if (field.type === 'integer' || field.type === 'float') {
    const text = stringInputValue(value).trim();
    const parsed = field.type === 'integer' ? Number.parseInt(text, 10) : Number(text);
    if (!text || !Number.isFinite(parsed)) return '必须是数字';
    if (field.type === 'integer' && !Number.isInteger(Number(text))) return '必须是整数';
    const min = numberConstraint(field, 'min');
    const max = numberConstraint(field, 'max');
    if (min !== null && parsed < min) return `不能小于 ${min}`;
    if (max !== null && parsed > max) return `不能大于 ${max}`;
  }
  if (field.type === 'enum' && Array.isArray(field.constraints.values)) {
    const allowed = field.constraints.values.map(String);
    if (!allowed.includes(String(value ?? ''))) return '取值不在允许范围内';
  }
  return null;
}

function fieldErrorsFromApi(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError)) return {};
  const fields = error.details.fields;
  if (!Array.isArray(fields)) return {};
  return fields.reduce<Record<string, string>>((acc, item) => {
    if (isRecord(item) && typeof item.path === 'string') {
      acc[item.path] = typeof item.message === 'string' ? item.message : '字段无效';
    }
    return acc;
  }, {});
}

function validateModelForm(
  editor: ModelEditor,
  models: ModelConfigSummary[],
): Record<string, string> {
  const errors: Record<string, string> = {};
  const id = editor.form.id.trim();
  if (!id) errors.id = '模型 ID 不能为空';
  if (
    editor.mode === 'new' &&
    models.some((model) => model.id.toLowerCase() === id.toLowerCase())
  ) {
    errors.id = '模型 ID 已存在';
  }
  if (!editor.form.model_name.trim()) errors.model_name = '模型名称不能为空';
  if (!THINK_EFFORT_OPTIONS.includes(editor.form.think_effort)) {
    errors.think_effort = '思考力度取值无效';
  }
  return errors;
}

function modelFromEditor(
  editor: ModelEditor,
  models: ModelConfigSummary[],
): ModelConfigSummary {
  const existing = models.find((model) => model.id === editor.originalId);
  const apiKey =
    editor.mode === 'new' || editor.form.apiKeyTouched
      ? editor.form.api_key
      : existing?.api_key ?? '';
  return {
    id: editor.form.id.trim(),
    provider: editor.form.provider.trim() || 'openai_compatible',
    url: editor.form.url.trim(),
    model_name: editor.form.model_name.trim(),
    api_key: apiKey,
    api_key_configured:
      editor.mode === 'new' || editor.form.apiKeyTouched
        ? Boolean(apiKey)
        : Boolean(existing?.api_key_configured),
    think_effort: editor.form.think_effort,
  };
}

function referencingModelPaths(config: ConfigFile, modelId: string): string[] {
  const paths: string[] = [];
  (['global_model_id', 'chat_model_id', 'comment_model_id'] as const).forEach(
    (field) => {
      if (config.defaults[field] === modelId) paths.push(`默认.${field}`);
      if (config.active[field] === modelId) paths.push(`当前.${field}`);
    },
  );
  return paths;
}

function pingBodyForModel(
  model: ModelConfigSummary,
  doc: ConfigDocument,
  maskedSecret: string,
): Parameters<typeof api.pingModel>[0] {
  const saved = doc.models.some((item) => item.id === model.id);
  const payload: Partial<ModelConfigSummary> = {
    id: model.id,
    provider: model.provider,
    url: model.url,
    model_name: model.model_name,
    think_effort: model.think_effort,
  };
  if (model.api_key && model.api_key !== maskedSecret) {
    payload.api_key = model.api_key;
  }
  return saved ? { model_id: model.id, model: payload } : { model: payload };
}

function pingBodyForEditor(
  editor: ModelEditor,
  doc: ConfigDocument,
): Parameters<typeof api.pingModel>[0] {
  const payload: Partial<ModelConfigSummary> = {
    id: editor.form.id.trim(),
    provider: editor.form.provider.trim(),
    url: editor.form.url.trim(),
    model_name: editor.form.model_name.trim(),
    think_effort: editor.form.think_effort,
  };
  if (editor.mode === 'new' || editor.form.apiKeyTouched) {
    payload.api_key = editor.form.api_key;
  }
  const saved = editor.originalId
    ? doc.models.some((model) => model.id === editor.originalId)
    : false;
  return saved && editor.originalId
    ? { model_id: editor.originalId, model: payload }
    : { model: payload };
}

function pingSummary(result: ModelPingResult): string {
  const tokens = result.tokens
    ? `token ${result.tokens.input ?? '?'} / ${result.tokens.output ?? '?'}`
    : 'token 未返回';
  const latency = result.latency_ms !== undefined ? `${result.latency_ms}ms` : '无延迟数据';
  return `${result.model} · ${latency} · ${tokens}`;
}
