import {
  Activity,
  Bot,
  Clock3,
  Database,
  Gauge,
  MessageSquareText,
  RefreshCcw,
  Settings,
  Signal,
  WifiOff,
} from 'lucide-react';
import type { ReactNode } from 'react';

import type { BackendConnection, RequestState, WindowCounts } from '../../app/types';
import { StatusPill } from '../../components/StatusPill';
import {
  chapterDisplayTitle,
  compactDataDir,
  compactModelName,
  formatDate,
  formatNumber,
  statusCopy,
} from '../../lib/formatters';
import type {
  ActivityItem,
  BookSummary,
  ChapterSummary,
  JobSummary,
  ReadingWindow,
  RuntimeInfo,
  SettingsSummary,
} from '../../types';

export function StatusCenter({
  request,
  runtime,
  settings,
  connection,
  events,
  selectedBook,
  activeChapter,
  currentWindow,
  windowCounts,
  jobs,
  onRetryWindow,
}: {
  request: RequestState;
  runtime: RuntimeInfo | null;
  settings: SettingsSummary | null;
  connection: BackendConnection;
  events: ActivityItem[];
  selectedBook: BookSummary | null;
  activeChapter: ChapterSummary | null;
  currentWindow: ReadingWindow | null;
  windowCounts: WindowCounts;
  jobs: JobSummary[];
  onRetryWindow: () => void;
}) {
  const contextLimit =
    settings?.context?.provider_context_limit_tokens ??
    settings?.context?.effective_input_budget ??
    0;
  const targetTokens =
    settings?.context?.attention_target_input_tokens ??
    settings?.window_l1?.focus_target_tokens ??
    settings?.window?.target_window_tokens ??
    0;

  return (
    <div className="status-center observability-section">
      <div className="panel-title">
        <Activity size={18} />
        <span>状态中心</span>
      </div>

      <section className="status-card main-status">
        <div>
          <strong>{request.label}</strong>
          <span>{request.detail || '暂无异常。'}</span>
        </div>
        <StatusPill status={request.status}>{statusCopy(request.status)}</StatusPill>
        {request.requestId && <code>request_id: {request.requestId}</code>}
      </section>

      <section className="status-grid">
        <MetricCard
          icon={<Database size={18} />}
          label="本地数据"
          value={compactDataDir(runtime?.data_dir)}
          title={runtime?.data_dir || '未连接'}
          tone={runtime ? 'good' : 'warn'}
        />
        <MetricCard
          icon={<Bot size={18} />}
          label="模型"
          value={compactModelName(runtime?.llm.model || settings?.llm.model)}
          title={runtime?.llm.model || settings?.llm.model || '未知'}
          detail={runtime?.llm.api_key_configured ? 'Key 已配置' : 'Key 未配置'}
          tone={runtime?.llm.api_key_configured ? 'good' : 'warn'}
        />
        <MetricCard
          icon={<Gauge size={18} />}
          label="上下文预算"
          value={formatNumber(contextLimit || targetTokens)}
          detail={targetTokens ? `目标 ${formatNumber(targetTokens)}` : '等待配置'}
          tone="info"
        />
        <MetricCard
          icon={connection === 'error' ? <WifiOff size={18} /> : <Signal size={18} />}
          label="后台事件"
          value={connection === 'open' ? '已订阅' : connection}
          detail={
            selectedBook && activeChapter ? chapterDisplayTitle(activeChapter) : '选择章节后订阅'
          }
          tone={connection === 'open' ? 'good' : connection === 'error' ? 'bad' : 'warn'}
        />
      </section>

      <WindowStatusCard
        currentWindow={currentWindow}
        windowCounts={windowCounts}
        jobs={jobs}
        onRetryWindow={onRetryWindow}
      />

      <section className="status-card">
        <div className="section-heading">
          <Clock3 size={18} />
          <strong>活动流</strong>
        </div>
        <ActivityFeed events={events} />
      </section>

      <section className="status-card">
        <div className="section-heading">
          <Settings size={18} />
          <strong>运行摘要</strong>
        </div>
        <dl className="runtime-list">
          <div>
            <dt>版本</dt>
            <dd>{runtime?.version || '未连接'}</dd>
          </div>
          <div>
            <dt>可观测</dt>
            <dd>{runtime?.observability.enabled ? runtime.observability.provider : '关闭'}</dd>
          </div>
          <div>
            <dt>验证模式</dt>
            <dd>{runtime?.verify_mode ? '开启' : '关闭'}</dd>
          </div>
          <div>
            <dt>阅读设置</dt>
            <dd>
              {settings
                ? `${settings.reader.font_size}px / ${settings.reader.line_height}`
                : '等待加载'}
            </dd>
          </div>
        </dl>
      </section>
    </div>
  );
}

function WindowStatusCard({
  currentWindow,
  windowCounts,
  jobs,
  onRetryWindow,
}: {
  currentWindow: ReadingWindow | null;
  windowCounts: WindowCounts;
  jobs: JobSummary[];
  onRetryWindow: () => void;
}) {
  if (!currentWindow) {
    return (
      <div className="window-card empty-window">
        <div>
          <Bot size={18} />
          <strong>AI 窗口等待触发</strong>
        </div>
        <span>暂无活动窗口。</span>
      </div>
    );
  }

  const readyPct =
    windowCounts.target > 0
      ? Math.round((windowCounts.ready / windowCounts.target) * 100)
      : 0;
  const retryable = currentWindow.status === 'failed' || currentWindow.status === 'done';

  return (
    <div className={`window-card window-${currentWindow.status}`}>
      <div className="window-card-header">
        <div>
          <Bot size={18} />
          <strong>窗口 {currentWindow.id}</strong>
        </div>
        <StatusPill
          status={
            currentWindow.status === 'done'
              ? 'success'
              : currentWindow.status === 'failed'
                ? 'error'
                : 'loading'
          }
        >
          {currentWindow.status}
        </StatusPill>
      </div>

      <div className="window-range">
        <span>
          覆盖 P{currentWindow.start_paragraph_idx + 1} - P
          {currentWindow.end_paragraph_idx + 1}
        </span>
        <span>
          焦点 P{currentWindow.focus_start_paragraph_idx + 1} - P
          {currentWindow.focus_end_paragraph_idx + 1}
        </span>
        <span>AI frontier P{currentWindow.assistant_frontier_paragraph_idx + 1}</span>
      </div>

      <div className="progress-meter" aria-label="评论准备进度">
        <span style={{ width: `${Math.min(100, readyPct)}%` }} />
      </div>
      <small>
        评论 {windowCounts.ready} / {windowCounts.target || '待估算'}
        {currentWindow.error ? ` · ${currentWindow.error}` : ''}
      </small>

      <div className="job-strip">
        {jobs.slice(0, 3).map((job) => (
          <span key={job.id} className={`job-chip job-${job.status}`}>
            #{job.id} {job.job_type} · {job.status}
          </span>
        ))}
      </div>

      {retryable && (
        <button className="soft-inline-button" onClick={onRetryWindow}>
          <RefreshCcw size={16} />
          重试 AI 窗口
        </button>
      )}
    </div>
  );
}

function MetricCard({
  icon,
  label,
  value,
  title,
  detail,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  title?: string;
  detail?: string;
  tone: ActivityItem['tone'];
}) {
  return (
    <div className={`metric-card tone-${tone}`}>
      <span className="metric-icon">{icon}</span>
      <small>{label}</small>
      <strong title={title || value}>{value}</strong>
      {detail && <span>{detail}</span>}
    </div>
  );
}

function ActivityFeed({ events }: { events: ActivityItem[] }) {
  if (!events.length) {
    return (
      <div className="empty-feed">
        <MessageSquareText size={18} />
        <span>暂无活动。</span>
      </div>
    );
  }
  return (
    <div className="activity-feed">
      {events.map((item) => (
        <div className={`activity-item tone-${item.tone}`} key={item.id}>
          <span className="activity-dot" />
          <div>
            <strong>{item.title}</strong>
            {item.detail && <small>{item.detail}</small>}
            {item.traceId && <code>trace_id: {item.traceId}</code>}
            {item.requestId && <code>request_id: {item.requestId}</code>}
          </div>
          <time>{formatDate(item.createdAt)}</time>
        </div>
      ))}
    </div>
  );
}
