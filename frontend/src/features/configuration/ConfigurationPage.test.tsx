import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ConfigDocument, SettingsSummary } from '../../types';
import { ConfigurationPage } from './ConfigurationPage';

const apiMocks = vi.hoisted(() => ({
  api: {
    config: vi.fn(),
    saveConfig: vi.fn(),
    runtime: vi.fn(),
    settings: vi.fn(),
    switchActiveModel: vi.fn(),
    pingModel: vi.fn(),
  },
}));

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>();
  return {
    ...actual,
    api: apiMocks.api,
  };
});

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });
}

function baseDocument(): ConfigDocument {
  return {
    config: {
      models: [
        {
          id: 'saved',
          provider: 'openai_compatible',
          url: 'https://saved.example/v1',
          model_name: 'saved-model',
          api_key_configured: true,
          api_key: '********',
          think_effort: '',
        },
      ],
      defaults: {
        global_model_id: 'saved',
        chat_model_id: 'saved',
        comment_model_id: 'saved',
      },
      active: {
        global_model_id: '',
        chat_model_id: '',
        comment_model_id: '',
      },
      groups: {},
    },
    models: [
      {
        id: 'saved',
        provider: 'openai_compatible',
        url: 'https://saved.example/v1',
        model_name: 'saved-model',
        api_key_configured: true,
        api_key: '********',
        think_effort: '',
      },
    ],
    defaults: {
      global_model_id: 'saved',
      chat_model_id: 'saved',
      comment_model_id: 'saved',
    },
    active: {
      global_model_id: '',
      chat_model_id: '',
      comment_model_id: '',
    },
    effective: {
      global: effectiveModel('global'),
      chat: effectiveModel('chat'),
      comment: effectiveModel('comment'),
      compaction: effectiveModel('compaction'),
    },
    metadata: {
      groups: {
        models: {
          label: '模型管理',
          description: '维护 LLM 连接配置。',
          fields: {},
          secret_policy: {
            masked_value: '********',
            unchanged_sentinel: '__vibe_reader_secret_unchanged__',
            readback: 'masked',
          },
          ignored_env: [],
          read_only_env: [],
        },
      },
      env_overrides: {},
      ignored_env: {},
      read_only_env: {},
      migrations: [],
    },
    runtime: {
      app: 'vibe-reader-mini',
      version: '0.1.0',
      data_dir: '/tmp/vibe-reader',
      verify_mode: false,
      llm: {
        base_url_configured: true,
        api_key_configured: true,
        model: 'saved-model',
        model_name: 'saved-model',
        provider: 'openai_compatible',
        source: 'catalog',
      },
      models: {
        catalog_count: 1,
        effective: {
          global: effectiveModel('global'),
          chat: effectiveModel('chat'),
          comment: effectiveModel('comment'),
          compaction: effectiveModel('compaction'),
        },
      },
      observability: { enabled: true, provider: 'otel' },
    },
    policy: {
      in_flight_model_switch:
        '进行中的 Chat 流和 running 评论任务沿用启动时模型；新请求和新任务使用更新后的当前配置。',
      compaction_model: 'Context Compaction Agent 与 Comment Agent 共用模型。',
    },
  };
}

function effectiveModel(agent: 'global' | 'chat' | 'comment' | 'compaction') {
  return {
    agent,
    model_id: 'saved',
    provider: 'openai_compatible',
    model_name: 'saved-model',
    think_effort: '',
    source: 'catalog',
    base_url_configured: true,
    api_key_configured: true,
  };
}

function settingsSummary(): SettingsSummary {
  return {
    models: baseDocument().models,
    defaults: baseDocument().defaults,
    active: baseDocument().active,
    effective: baseDocument().effective,
    reader: {},
    llm: {
      api_key_configured: true,
      model: 'saved-model',
      model_name: 'saved-model',
      provider: 'openai_compatible',
      source: 'catalog',
    },
  };
}

function renderPage(onDirtyChange = vi.fn()) {
  const queryClient = createQueryClient();
  render(<ConfigurationPage onDirtyChange={onDirtyChange} />, {
    wrapper: createWrapper(queryClient),
  });
  return { onDirtyChange };
}

async function addDraftModel() {
  fireEvent.click(await screen.findByRole('button', { name: /新建模型/ }));
  fireEvent.change(screen.getByLabelText('模型 ID'), {
    target: { value: 'draft' },
  });
  fireEvent.change(screen.getByLabelText('模型名称'), {
    target: { value: 'draft-model' },
  });
  fireEvent.click(screen.getByRole('button', { name: /写入草稿/ }));
}

describe('ConfigurationPage', () => {
  beforeEach(() => {
    apiMocks.api.config.mockResolvedValue(baseDocument());
    apiMocks.api.saveConfig.mockResolvedValue(baseDocument());
    apiMocks.api.runtime.mockResolvedValue(baseDocument().runtime);
    apiMocks.api.settings.mockResolvedValue(settingsSummary());
    apiMocks.api.switchActiveModel.mockResolvedValue(baseDocument());
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('refetches runtime and settings after saving configuration', async () => {
    renderPage();
    await addDraftModel();

    fireEvent.click(screen.getByRole('button', { name: /^保存$/ }));

    await waitFor(() => expect(apiMocks.api.saveConfig).toHaveBeenCalled());
    await waitFor(() => expect(apiMocks.api.runtime).toHaveBeenCalled());
    expect(apiMocks.api.settings).toHaveBeenCalled();
  });

  it('does not offer unsaved draft models for active switching', async () => {
    renderPage();
    await addDraftModel();

    const chatSwitch = screen.getByLabelText(/切换 Chat 当前/) as HTMLSelectElement;
    const optionValues = Array.from(chatSwitch.options).map((option) => option.value);

    expect(optionValues).toContain('saved');
    expect(optionValues).not.toContain('draft');
  });

  it('treats model editor input as dirty before it is written to the draft', async () => {
    const { onDirtyChange } = renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /新建模型/ }));
    fireEvent.change(screen.getByLabelText('模型 ID'), {
      target: { value: 'draft' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^保存$/ }));

    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    expect(screen.getByText('请先点击“写入草稿”，或关闭模型编辑后再保存。')).toBeTruthy();
    expect(apiMocks.api.saveConfig).not.toHaveBeenCalled();
  });
});
