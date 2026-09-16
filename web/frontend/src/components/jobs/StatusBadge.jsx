/**
 * Job status pill. Lifted from Dashboard so Dashboard, Queue and the prompt
 * host all label a job the same way.
 */
const MAP = {
  idle:              { label: 'Idle',            cls: 'badge-slate'   },
  running:           { label: 'Translating…',    cls: 'badge-indigo'  },
  waiting:           { label: 'Paused (limit)',  cls: 'badge-amber'   },
  awaiting_review:   { label: 'Review needed',   cls: 'badge-amber'   },
  awaiting_json_fix: { label: 'JSON Fix',        cls: 'badge-amber'   },
  awaiting_chapter_conflict: { label: 'Chapter conflict', cls: 'badge-amber' },
  complete:          { label: 'Complete',        cls: 'badge-emerald' },
  error:             { label: 'Error',           cls: 'badge-rose'    },
}

export default function StatusBadge({ status }) {
  const { label, cls } = MAP[status] || MAP.idle
  return <span className={cls}>{label}</span>
}
